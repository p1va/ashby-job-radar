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
import sources


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
        "company": job["_slug"], "companyName": radar.job_company(job),
        "source": job.get("source") or sources.provider(job["_slug"]),
        "companyWebsite": public_url(job.get("companyWebsite")),
        "companyLogoUrl": public_url(job.get("companyLogoUrl")),
        "companyIntroduction": job.get("companyIntroduction") or "",
        "location": job["_loc"], "locations": locations,
        "style": job["_style"], "salary": job["_comp"],
        "employment": job.get("employmentType") or "",
        "department": job.get("department") or "", "team": job.get("team") or "",
        "published": job["_pub"].isoformat() if job["_pub"] else None,
        "description": description(job), "url": url, "applyUrl": apply,
    }


def public_url(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ("https", "http") and parsed.hostname else ""
    except ValueError:
        return ""


def write_site(out: Path, jobs: list[dict], report: dict, profiles: dict | None = None, featured=()):
    out.mkdir(parents=True, exist_ok=True)
    profiles = profiles or {}
    companies = {}
    for job in jobs:
        slug = job["company"]
        if slug not in companies:
            profile = profiles.get(slug, {})
            companies[slug] = {"name": profile.get("name") or job["companyName"],
                               "website": public_url(profile.get("website") or job.get("companyWebsite")),
                               "logoUrl": public_url(profile.get("logo_url") or job.get("companyLogoUrl")),
                               "introduction": description({"descriptionHtml": profile.get("board_introduction_html") or ""}) or job.get("companyIntroduction") or "",
                               "featured": slug in featured, "roles": 0}
        companies[slug]["roles"] += 1
    jobs = [{**job, "companyName": companies[job["company"]]["name"]} for job in jobs]
    # A stable public lookup also supplies the independent enrichment workflow.
    (out / "companies.json").write_text(json.dumps(companies, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    data = json.dumps({"generatedAt": report["generated_at"], "partial": bool(report.get("boards_failed")), "jobs": jobs, "companies": companies},
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
    parser.add_argument("--cache", action="store_true", help="save normalized board responses for offline replay")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--source-info", type=Path)
    parser.add_argument("--company-info", type=Path, default=ROOT / "company-info/companies.json")
    parser.add_argument("--featured", type=Path, default=ROOT / "company-info/featured.json")
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    if args.out.exists():
        parser.error("Output directory must be new (avoids publishing stale files)")
    cfg = read_object(args.config)
    active_sources = sources.enabled(cfg)
    profiles = read_object(args.company_info, required=False)
    if not all(isinstance(profile, dict) for profile in profiles.values()):
        parser.error("company-info entries must be objects")
    featured = json.loads(args.featured.read_text()) if args.featured.exists() else []
    if not isinstance(featured, list) or not all(isinstance(slug, str) for slug in featured):
        parser.error("featured must be a list of company slugs")
    invalid = []
    known = {slug.lower() for slug in read_object(args.registry, required=False) if not slug.startswith("_")}
    for path in (args.companies or [ROOT / "companies.txt"]) + [ROOT / "embedded-boards/boards.txt"]:
        known.update(slug.lower() for slug in radar.load_slugs(path, invalid))
    fixtures = {unquote(path.stem).lower(): path for path in args.fixtures.glob("*.json")} if args.fixtures else {}
    selected = {sources.board_key(slug) for slug in args.board} if args.board else fixtures if args.fixtures else known
    slugs = sources.select(selected, cfg)
    if args.fixtures:
        slugs = sorted(key for key in selected if sources.provider(key) in active_sources)
    use_workable = "workable" in active_sources and not args.fixtures and not args.board
    if not slugs and not use_workable:
        parser.error("No company boards selected")

    def fetch(slug):
        if not args.fixtures:
            return radar.fetch_board(slug, cfg) if sources.provider(slug) == "greenhouse" else radar.fetch_board(slug)
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
    workable_ok, scan_failed = False, False
    if use_workable:
        print("[catalog] Scanning Workable Germany", flush=True)
        try:
            boards.update(radar.fetch_workable())
            workable_ok = True
        except Exception as error:
            scan_failed = True
            failures.append({"company": "workable search", "error": f"{type(error).__name__}: {error}"})
    if args.cache:
        from urllib.parse import quote
        (args.out / "boards").mkdir(parents=True)
        for key, postings in boards.items():
            (args.out / "boards" / (quote(key, safe="") + ".json")).write_text(json.dumps({"jobs": postings}), encoding="utf-8")
    # No sent-history or publication cutoff: this is the current matching catalog.
    matches, counts = select_matches(boards, cfg, {})
    jobs = [public_job(job) for job in matches]
    report = {"generated_at": started, "mode": "fixtures" if args.fixtures else "live",
              "boards_requested": len(slugs), "boards_ok": len(boards),
              "boards_not_found": len(missing), "boards_failed": len(failures),
              "jobs_read": sum(map(len, boards.values())), "roles": len(jobs),
              "companies": len({job["company"] for job in jobs}), "selection": counts,
              "source": read_object(args.source_info) if args.source_info else {}, "failures": failures}
    report["sources"] = active_sources
    report["roles_by_source"] = {source: sum(job["source"] == source for job in jobs) for source in active_sources}
    report["workable_scan_complete"] = workable_ok
    report["invalid_companies"] = [{"value": value, "error": error} for value, error in invalid]
    # A few unavailable boards should not freeze thousands of successful checks.
    # Apply the allowance per provider so a small source's outage cannot disappear
    # inside the larger Ashby count. A failed global scan always blocks publication.
    exceeded = [source for source in active_sources if source != "workable"
                and sum(sources.provider(row["company"]) == source for row in failures
                        if row["company"] != "workable search")
                > sum(sources.provider(key) == source for key in slugs) // 20]
    report["failure_budget_exceeded"] = exceeded
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (args.out / "summary.md").write_text(
        f"Catalog: **{len(jobs)} roles at {report['companies']} companies**. "
        f"{len(boards)} boards fetched; {len(missing)} no longer found; {len(failures)} failed.\n", encoding="utf-8")
    if scan_failed or exceeded or (not boards and not workable_ok):
        print("[catalog] Incomplete fetch; keeping the previous published catalog.", file=sys.stderr)
        return 1
    write_site(args.out / "site", jobs, report, profiles, featured)
    print(f"[catalog] Built {len(jobs)} roles at {report['companies']} companies: {args.out / 'site'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
