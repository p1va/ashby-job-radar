#!/usr/bin/env python3
"""Build a static catalog of every currently listed job matching the radar config."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "catch-up"))
from preview import read_object, select_matches
import ashby_radar as radar


class PlainText(HTMLParser):
    """Keep readable paragraph breaks without executing or publishing employer HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif not self.hidden and tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n" if tag == "br" else "\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def description(job: dict) -> str:
    if job.get("descriptionPlain"):
        return job["descriptionPlain"].strip()
    parser = PlainText()
    parser.feed(job.get("descriptionHtml") or "")
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.parts)).strip()


def public_job(job: dict) -> dict:
    url = job["jobUrl"]
    apply = job.get("applyUrl") or url
    if urlsplit(apply).scheme not in ("https", "http"):
        apply = url
    locations = []
    for location in [job] + (job.get("secondaryLocations") or []):
        address = (location.get("address") or {}).get("postalAddress") or {}
        label = location.get("location") or ", ".join(dict.fromkeys(
            str(address[key]) for key in ("addressLocality", "addressRegion", "addressCountry") if address.get(key)))
        if label and label not in locations:
            locations.append(label)
    return {
        "id": str(job.get("id") or url), "title": job["title"].strip(),
        "company": job["_slug"], "companyName": radar.company_name(job["_slug"]),
        "location": job["_loc"], "locations": locations,
        "style": job["_style"], "salary": job["_comp"],
        "employment": job.get("employmentType") or "",
        "department": job.get("department") or "", "team": job.get("team") or "",
        "published": job["_pub"].isoformat() if job["_pub"] else None,
        "description": description(job), "url": url, "applyUrl": apply,
    }


def write_site(out: Path, jobs: list[dict], report: dict):
    out.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"generatedAt": report["generated_at"], "jobs": jobs},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    filename = "jobs-" + hashlib.sha256(data).hexdigest()[:12] + ".json"
    (out / filename).write_bytes(data)
    replacements = {"DATA_FILE": filename, "ROLE_COUNT": f"{len(jobs):,}",
                    "COMPANY_COUNT": str(len({job["company"] for job in jobs}))}
    for name in ("app.js", "styles.css"):
        content = (HERE / name).read_bytes()
        hashed = name.replace(".", "-" + hashlib.sha256(content).hexdigest()[:12] + ".", 1)
        (out / hashed).write_bytes(content)
        replacements[name.upper().replace(".", "_")] = hashed
    page = (HERE / "index.html").read_text(encoding="utf-8")
    for key, value in replacements.items():
        page = page.replace("{{" + key + "}}", value)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "favicon.svg").write_bytes((HERE / "favicon.svg").read_bytes())
    (out / ".nojekyll").touch()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--registry", type=Path, default=ROOT / "boards.json")
    parser.add_argument("--companies", type=Path, action="append")
    parser.add_argument("--board", action="append", help="limit to these slugs; repeat as needed")
    parser.add_argument("--fixtures", type=Path, help="read saved <slug>.json responses instead of fetching")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--source-info", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    if args.out.exists():
        parser.error("Output directory must be new (avoids publishing stale files)")
    cfg = read_object(args.config)
    known = {slug.lower() for slug in read_object(args.registry, required=False) if not slug.startswith("_")}
    for path in (args.companies or [ROOT / "companies.txt"]) + [ROOT / "embedded-boards/boards.txt"]:
        known.update(slug.lower() for slug in radar.load_slugs(path))
    fixtures = {unquote(path.stem).lower(): path for path in args.fixtures.glob("*.json")} if args.fixtures else {}
    slugs = sorted({slug.strip().lower() for slug in args.board} if args.board else fixtures if args.fixtures else known)
    if not slugs:
        parser.error("No company boards selected")

    def fetch(slug):
        if not args.fixtures:
            return radar.fetch_board(slug)
        try:
            jobs = read_object(fixtures[slug])["jobs"]
            if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
                raise ValueError("Fixture has no valid jobs list")
            return slug, jobs, None
        except (KeyError, ValueError, OSError) as error:
            return slug, None, str(error)

    boards, failures, missing = {}, [], []
    started = datetime.now(timezone.utc).isoformat()
    print(f"[catalog] Checking {len(slugs)} boards", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for index, (slug, jobs, error) in enumerate(pool.map(fetch, slugs), 1):
            if jobs is not None:
                boards[slug] = jobs
            elif error == "HTTP 404":
                missing.append(slug)
            else:
                failures.append({"company": slug, "error": error})
            if index % 100 == 0 or index == len(slugs):
                print(f"[catalog] {index}/{len(slugs)} checked · {len(boards)} fetched", flush=True)
    # No sent-history or publication cutoff: this is the current matching catalog.
    matches, counts = select_matches(boards, cfg, {})
    jobs = [public_job(job) for job in matches]
    report = {"generated_at": started, "mode": "fixtures" if args.fixtures else "live",
              "boards_requested": len(slugs), "boards_ok": len(boards),
              "boards_not_found": len(missing), "boards_failed": len(failures),
              "jobs_read": sum(map(len, boards.values())), "roles": len(jobs),
              "companies": len({job["company"] for job in jobs}), "selection": counts,
              "source": read_object(args.source_info) if args.source_info else {}, "failures": failures}
    args.out.mkdir(parents=True)
    (args.out / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (args.out / "summary.md").write_text(
        f"Catalog: **{len(jobs)} roles at {report['companies']} companies**. "
        f"{len(boards)} boards fetched; {len(missing)} no longer found; {len(failures)} failed.\n", encoding="utf-8")
    if failures or not boards:
        print("[catalog] Incomplete fetch; keeping the previous published catalog.", file=sys.stderr)
        return 1
    write_site(args.out / "site", jobs, report)
    print(f"[catalog] Built {len(jobs)} roles at {report['companies']} companies: {args.out / 'site'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
