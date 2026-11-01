#!/usr/bin/env python3
"""Combine independent provider collections into a catalog and read-only catch-up digest."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import build as catalog
import preview
import sources


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return result


def read_collection(data, source, cfg, provenance, now):
    report = data["report"]
    if (data.get("version") != 1 or data.get("provider") != source
            or data.get("config_hash") != catalog.config_hash(cfg)
            or report.get("sources") != [source] or report.get("source") != provenance):
        raise ValueError("Collection has incompatible preferences, provenance or provider")
    age = now - parse_time(report["generated_at"])
    if not -timedelta(minutes=5) <= age <= timedelta(hours=2):
        raise ValueError("Collection is outside this run's freshness window")
    if report.get("failure_budget_exceeded") or (source == "workable" and not report.get("workable_scan_complete")):
        raise ValueError("Provider scan did not complete")
    if source != "workable" and report["boards_failed"] > report["boards_requested"] // 20:
        raise ValueError("Provider failure allowance exceeded")
    boards = data["boards"]
    if not isinstance(boards, dict):
        raise ValueError("Collection has no board mapping")
    for key, jobs in boards.items():
        if sources.provider(key) != source or not isinstance(jobs, list):
            raise ValueError("Collection contains a foreign provider or invalid board")
        for job in jobs:
            if not isinstance(job, dict) or job.get("source", source) != source:
                raise ValueError("Collection contains an invalid job or source")
            if source != "ashby" and not str(job.get("id", "")).startswith(source + ":"):
                raise ValueError("Collection contains an invalid native job ID")
    matches, _ = preview.select_matches(boards, cfg, {})
    if len(matches) != report["roles"] or sum(map(len, boards.values())) != report["roles"]:
        raise ValueError("Collection's matching jobs disagree with its report")
    return boards, report


def previous_jobs(previous, source, fingerprint, now, max_age):
    if previous.get("configHash") != fingerprint:
        return [], None
    status = (previous.get("sourceStatus") or {}).get(source, {})
    if status.get("state") not in ("fresh", "partial", "stale"):
        return [], None
    try:
        updated = status["updatedAt"]
        age = now - parse_time(updated)
        if not -timedelta(minutes=5) <= age <= max_age:
            return [], None
        jobs = [job for job in previous["jobs"] if job["source"] == source]
        if any(sources.provider(job["company"]) != source or urlsplit(job["url"]).scheme not in ("https", "http") for job in jobs):
            return [], None
        return jobs, updated
    except (KeyError, TypeError, ValueError, AttributeError):
        return [], None


def combine(collections, cfg, provenance, previous, now, max_age):
    boards, reports, statuses, retained = {}, {}, {}, []
    available = {}
    # download-artifact can flatten a single artifact or preserve subfolders.
    for path in sorted(collections.rglob("collection.json")):
        try:
            data = preview.read_object(path)
            available.setdefault(data.get("provider"), []).append(data)
        except (OSError, ValueError, TypeError) as error:
            print(f"[combine] Ignoring invalid collection {path}: {error}", file=sys.stderr)
    for source in sources.enabled(cfg):
        try:
            candidates = available.get(source, [])
            if len(candidates) != 1:
                raise ValueError(f"Expected one {source} collection; found {len(candidates)}")
            collected, report = read_collection(candidates[0], source, cfg, provenance, now)
            boards.update(collected)
            reports[source] = report
            statuses[source] = {"state": "partial" if report["boards_failed"] else "fresh",
                                "updatedAt": report["generated_at"]}
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            jobs, updated = previous_jobs(previous, source, catalog.config_hash(cfg), now, max_age)
            retained.extend(jobs)
            statuses[source] = {"state": "stale" if updated else "unavailable", "updatedAt": updated,
                                "error": str(error)}
    matches, counts = preview.select_matches(boards, cfg, {})
    jobs = [catalog.public_job(job) for job in matches] + retained
    partial = any(status["state"] != "fresh" for status in statuses.values())
    report = {"generated_at": now.isoformat(), "mode": "combined", "config_hash": catalog.config_hash(cfg),
              "source": provenance, "sources": sources.enabled(cfg), "source_status": statuses,
              "provider_reports": reports, "partial": partial,
              "boards_requested": sum(r["boards_requested"] for r in reports.values()),
              "boards_ok": sum(r["boards_ok"] for r in reports.values()),
              "boards_not_found": sum(r["boards_not_found"] for r in reports.values()),
              "boards_failed": sum(r["boards_failed"] for r in reports.values()),
              "jobs_read": sum(r["jobs_read"] for r in reports.values()),
              "roles": len(jobs), "companies": len({job["company"] for job in jobs}),
              "roles_by_source": {source: sum(job["source"] == source for job in jobs) for source in statuses},
              "selection": counts, "fresh_sources": list(reports),
              "unavailable_sources": [source for source, status in statuses.items() if status["state"] in ("stale", "unavailable")],
              "retained_roles": len(retained), "stale_max_hours": max_age.total_seconds() / 3600}
    return jobs, boards, report


def load_previous(url):
    page = catalog.radar.request(url).decode("utf-8")
    filename = re.search(r"jobs-[a-f0-9]+\.json", page)
    if not filename:
        raise ValueError("Published page has no catalog data link")
    return catalog.radar.request(url.rstrip("/") + "/" + filename[0], parse=json.loads)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collections", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seen", type=Path, required=True)
    parser.add_argument("--source-info", type=Path)
    previous = parser.add_mutually_exclusive_group()
    previous.add_argument("--previous", type=Path)
    previous.add_argument("--previous-url")
    parser.add_argument("--max-stale-hours", type=float, default=48)
    parser.add_argument("--company-info", type=Path, default=catalog.ROOT / "company-info/companies.json")
    parser.add_argument("--featured", type=Path, default=catalog.ROOT / "company-info/featured.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists() or not 0 <= args.max_stale_hours <= 168:
        parser.error("Output must be new; max stale hours must be between 0 and 168")
    cfg = preview.read_object(args.config)
    seen = preview.read_object(args.seen)
    provenance = preview.read_object(args.source_info) if args.source_info else {}
    baseline = preview.read_object(args.previous) if args.previous else {}
    if args.previous_url:
        try:
            baseline = load_previous(args.previous_url)
        except Exception as error:
            print(f"[combine] Previous catalog unavailable: {error}", file=sys.stderr)
    now = datetime.now(timezone.utc)
    jobs, boards, report = combine(args.collections, cfg, provenance, baseline, now, timedelta(hours=args.max_stale_hours))
    args.out.mkdir(parents=True)
    (args.out / "run.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [f"Catalog: **{len(jobs)} roles at {report['companies']} companies**.", "",
             "| Source | Status | Roles | Last collected |", "| --- | --- | ---: | --- |"]
    for source, status in report["source_status"].items():
        lines.append(f"| {source} | {status['state']} | {report['roles_by_source'][source]} | {status['updatedAt'] or '—'} |")
    lines += ["", "Digest preview uses fresh collections only; no email or delivery-state writes."]
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not report["fresh_sources"]:
        print("[combine] No source refreshed successfully; keeping the previous publication.", file=sys.stderr)
        return 1
    profiles = preview.read_object(args.company_info, required=False)
    featured = json.loads(args.featured.read_text()) if args.featured.exists() else []
    catalog.write_site(args.out / "site", jobs, report, profiles, featured)
    unseen, counts = preview.select_matches(boards, cfg, seen)
    digest_report = {**report, **counts, "matches": len(unseen),
                     "history_retention_days": cfg.get("forget_after_days", 60),
                     "workable_scan_complete": "workable" in report["fresh_sources"]}
    digest = args.out / "digest"
    digest.mkdir()
    (digest / "digest.md").write_text(preview.render_md(unseen, digest_report), encoding="utf-8")
    (digest / "digest.html").write_text(preview.render_html(unseen, digest_report), encoding="utf-8")
    rows = [preview.public_row(job) for job in unseen]
    (digest / "matches.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    preview.write_csv(digest / "matches.csv", rows, ["id", "title", "company", "location", "work_style", "german_required", "compensation", "published", "url"])
    (digest / "run.json").write_text(json.dumps(digest_report, indent=2) + "\n", encoding="utf-8")
    print(f"[combine] {len(jobs)} catalog roles; {len(unseen)} unseen roles in fresh collections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
