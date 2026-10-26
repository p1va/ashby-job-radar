#!/usr/bin/env python3
"""Preview every currently listed matching job absent from saved sent history."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ashby_radar as radar


def read_object(path: Path, required: bool = True) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise ValueError(f"Missing input: {path}")
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def company_groups(matches: list[dict]) -> list[tuple[str, list[dict]]]:
    groups = {}
    for job in matches:
        groups.setdefault(job["_slug"], []).append(job)
    return [(slug, sorted(groups[slug], key=lambda job: job["_pub"] or
                         datetime.min.replace(tzinfo=timezone.utc), reverse=True))
            for slug in sorted(groups, key=lambda slug: (radar.company_name(slug).casefold(), slug))]


def select_matches(boards: dict, cfg: dict, seen: dict) -> tuple[list[dict], dict]:
    """Reuse the radar's predicates, with no publication cutoff or result cap."""
    matches, encountered = [], set()
    counts = {"matching_jobs": 0, "already_seen": 0, "duplicate_matches": 0,
              "untrackable_matches": 0, "matches_without_date": 0}
    for slug, jobs in boards.items():
        for job in jobs:
            title = job.get("title") or ""
            if job.get("isListed") is False or not radar.word_hit(title, cfg["title_include"]):
                continue
            if radar.word_hit(title, cfg["title_exclude"]):
                continue
            location = radar.match_location(job, cfg)
            style = radar.work_style(job)
            if not location or not radar.in_focus(location, style, cfg):
                continue
            url = job.get("jobUrl") or ""
            if not url or urllib.parse.urlsplit(url).scheme not in ("https", "http"):
                counts["untrackable_matches"] += 1
                continue
            keys = {str(job["id"]), url} if job.get("id") else {url}
            if encountered & keys:
                counts["duplicate_matches"] += 1
                continue
            encountered.update(keys)
            counts["matching_jobs"] += 1
            if any(key in seen for key in keys):
                counts["already_seen"] += 1
                continue
            try:
                published = radar.parse_date(job.get("publishedAt"))
            except (TypeError, AttributeError, ValueError):
                published = None
            if published and published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published is None:
                counts["matches_without_date"] += 1
            matches.append({**job, "_slug": slug, "_loc": location, "_style": style,
                            "_pub": published, "_german": radar.german_required(job, cfg),
                            "_comp": radar.compensation(job)})
    return [job for _, jobs in company_groups(matches) for job in jobs], counts


def public_row(job: dict) -> dict:
    return {"id": job.get("id"), "title": job["title"], "company": job["_slug"],
            "location": job["_loc"], "work_style": job["_style"],
            "german_required": job["_german"], "compensation": job["_comp"],
            "published": job["_pub"].isoformat() if job["_pub"] else None, "url": job["jobUrl"]}


def job_meta(job: dict) -> str:
    date = job["_pub"].strftime("%d %b %Y") if job["_pub"] else "Date not provided"
    return " · ".join(filter(None, [job["_loc"], job["_style"], job["_comp"] or "Salary not listed",
                                    date, job["_german"]]))


def report_text(report: dict) -> str:
    text = (f"{report['matches']} unseen open roles · {report['boards_ok']} companies checked · "
            f"{report['already_seen']} matching roles already recorded · no publication cutoff")
    if report["boards_failed"]:
        text += f". {report['boards_failed']} boards unavailable; results are incomplete"
    return text


def retention_note(report: dict) -> str:
    return (f"Sent history is retained for {report['history_retention_days']} days by the radar. "
            "Older jobs absent from that saved history may have been sent previously.")


def render_md(matches: list[dict], report: dict, summary: bool = False) -> str:
    lines = ["# Catch-up preview", "", report_text(report), "",
             "Companies A–Z; newest roles first within each company.", "", retention_note(report), ""]
    if not matches:
        lines.append("No unseen matching jobs found." if report["boards_ok"] else
                     "No boards could be fetched; matching jobs could not be checked.")
    remaining = 20 if summary else len(matches)
    for slug, jobs in company_groups(matches):
        if not remaining:
            break
        shown = jobs[:remaining]
        count = str(len(jobs)) if len(shown) == len(jobs) else f"{len(shown)} of {len(jobs)}"
        lines += [f"## {radar.company_name(slug)} — {count} {'role' if len(jobs) == 1 else 'roles'}", ""]
        for job in shown:
            lines += [f"### [{job['title']}]({job['jobUrl']})", job_meta(job), ""]
        remaining -= len(shown)
    if summary and len(matches) > 20:
        lines += [f"Showing the first 20 of {len(matches)} roles. Download the artifact for the complete report.", ""]
    return "\n".join(lines) + "\n"


def render_html(matches: list[dict], report: dict) -> str:
    e = html.escape
    sections = []
    for slug, jobs in company_groups(matches):
        heading = f'{e(radar.company_name(slug))} <span>{len(jobs)} {"role" if len(jobs) == 1 else "roles"}</span>'
        roles = "".join(f'<article><h3><a href="{e(job["jobUrl"])}">{e(job["title"])}</a></h3>'
                        f'<p>{e(job_meta(job))}</p></article>' for job in jobs)
        sections.append(f'<section class="company"><h2>{heading}</h2>{roles}</section>')
    rows = "".join(sections)
    if not matches:
        rows = "<p>No unseen matching jobs found.</p>" if report["boards_ok"] else "<p>No boards could be fetched.</p>"
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Catch-up preview</title><style>body{font:16px/1.5 system-ui,sans-serif;'
            'max-width:900px;margin:32px auto;padding:0 20px;color:#172033}'
            'h1{font-size:30px}h2{font-size:23px;margin-bottom:6px}h2 span{font-size:15px;'
            'font-weight:400;color:#64748b}h3{font-size:18px;margin:0 0 6px}a{color:#1d4ed8}'
            '.company{margin-top:28px}article{border-top:1px solid #ddd;padding:14px 0}p{color:#475569}'
            '</style><body><h1>Catch-up preview</h1>'
            f'<p>{e(report_text(report))}</p><p>Companies A–Z; newest roles first within each company.</p>'
            f'<p>{e(retention_note(report))}</p>{rows}</body></html>\n')


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def provenance(path: Path) -> dict:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} if path.is_file() else {"path": str(path), "missing": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=ROOT / "config.json")
    ap.add_argument("--seen", type=Path, default=ROOT / "seen.json", help="sent-history snapshot; must exist")
    ap.add_argument("--registry", type=Path, default=ROOT / "boards.json")
    ap.add_argument("--companies", type=Path, action="append", help="company list; repeat to combine sources")
    ap.add_argument("--embedded", type=Path, default=ROOT / "embedded-boards/boards.txt")
    ap.add_argument("--board", action="append", help="check only this slug; repeat for a subset")
    ap.add_argument("--fixtures", type=Path, help="replay cached <slug>.json board responses instead of fetching")
    ap.add_argument("--cache", action="store_true", help="save board responses under the output directory for replay")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--source-info", type=Path, help="optional JSON provenance for supplied input snapshots")
    ap.add_argument("--out", type=Path, help="new directory, default data/exploration/catch-up-<UTC time>")
    args = ap.parse_args(argv)
    if not 1 <= args.workers <= 32:
        ap.error("--workers must be between 1 and 32")
    company_files = args.companies or [ROOT / "companies.txt"]
    try:
        cfg = read_object(args.config)
        seen = read_object(args.seen)
        registry = read_object(args.registry, required=False)
        source_info = read_object(args.source_info) if args.source_info else {}
        known = {slug.lower() for slug in registry if not slug.startswith("_")}
        for path in company_files + [args.embedded]:
            known.update(slug.lower() for slug in radar.load_slugs(path))
        slugs = sorted({slug.strip().lower() for slug in args.board}) if args.board else sorted(known)
        fixtures = {urllib.parse.unquote(p.stem).lower(): p for p in args.fixtures.glob("*.json")} if args.fixtures else {}
        if args.fixtures:
            slugs = sorted(set(fixtures) & set(slugs)) if args.board else sorted(fixtures)
        if not slugs:
            raise ValueError("No company boards selected")
    except (ValueError, OSError) as error:
        ap.error(str(error))
    now = datetime.now(timezone.utc)
    out = args.out or ROOT / "data/exploration" / now.strftime("catch-up-%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    boards, statuses = {}, []

    def fetch(slug):
        if not args.fixtures:
            return radar.fetch_board(slug)
        try:
            payload = read_object(fixtures[slug])
            if not isinstance(payload.get("jobs"), list) or not all(isinstance(j, dict) for j in payload["jobs"]):
                raise ValueError("Fixture has no valid jobs list")
            return slug, payload["jobs"], None
        except (ValueError, OSError) as error:
            return slug, None, str(error)

    print(f"[catch-up] Checking {len(slugs)} boards against {sum(not k.startswith('_') for k in seen)} saved job IDs", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (slug, jobs, error) in enumerate(pool.map(fetch, slugs), 1):
            status = "not found" if error == "HTTP 404" else "error" if jobs is None else "ok"
            statuses.append({"company": slug, "status": status, "jobs": len(jobs) if jobs is not None else None, "error": error})
            if jobs is not None:
                boards[slug] = jobs
                if args.cache:
                    path = out / "boards" / (urllib.parse.quote(slug, safe="") + ".json")
                    path.parent.mkdir(exist_ok=True)
                    path.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            if i % 100 == 0 or i == len(slugs):
                print(f"[catch-up] {i}/{len(slugs)} checked · {len(boards)} fetched", flush=True)
    matches, counts = select_matches(boards, cfg, seen)
    errors = [row for row in statuses if row["status"] == "error"]
    report = {"generated_at": now.isoformat(), "mode": "fixtures" if args.fixtures else "live", "preview": True,
              "publication_cutoff": None, "result_limit": None, "boards_requested": len(slugs),
              "boards_ok": len(boards), "boards_not_found": sum(r["status"] == "not found" for r in statuses),
              "boards_failed": len(errors), "jobs_read": sum(len(jobs) for jobs in boards.values()),
              **counts, "matches": len(matches), "matching_companies": len({j["_slug"] for j in matches}),
              "grouping": "company", "company_order": "alphabetical", "job_order": "newest_first_within_company",
              "saved_seen_ids": sum(not k.startswith("_") for k in seen),
              "history_retention_days": cfg.get("forget_after_days", 60), "history_last_run": seen.get("_last_run"),
              "inputs": {"config": provenance(args.config), "seen": provenance(args.seen), "registry": provenance(args.registry),
                         "companies": [provenance(p) for p in company_files], "embedded": provenance(args.embedded)},
              "source": source_info, "failures": errors}
    rows = [public_row(job) for job in matches]
    (out / "matches.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(out / "matches.csv", rows, ["id", "title", "company", "location", "work_style", "german_required", "compensation", "published", "url"])
    write_csv(out / "boards.csv", statuses, ["company", "status", "jobs", "error"])
    (out / "digest.md").write_text(render_md(matches, report), encoding="utf-8")
    (out / "digest.html").write_text(render_html(matches, report), encoding="utf-8")
    (out / "summary.md").write_text(render_md(matches, report, summary=True), encoding="utf-8")
    (out / "run.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[catch-up] {len(matches)} unseen matches · {counts['already_seen']} matching jobs skipped as seen · report: {out}")
    return 1 if errors or not boards else 0


if __name__ == "__main__":
    sys.exit(main())
