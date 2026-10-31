#!/usr/bin/env python3
"""Backfill boards from Common Crawl, an Athena CSV or a seed list. No email."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ashby_radar as radar
import sources

PATTERNS = {"ashby": ["jobs.ashbyhq.com/*"], "greenhouse": [
    "job-boards.eu.greenhouse.io/*", "job-boards.greenhouse.io/*", "boards.greenhouse.io/*"]}


def collect_crawl(source, crawls, patterns, out, found, completed):
    catalog = radar.request("https://index.commoncrawl.org/collinfo.json", parse=json.loads)
    if not isinstance(catalog, list) or not all(isinstance(c, dict) and c.get("id") for c in catalog):
        raise ValueError("Common Crawl returned an invalid crawl catalog")
    for crawl in catalog[:crawls]:
        for pattern in patterns:
            base = "https://index.commoncrawl.org/" + crawl["id"] + "-index?" + urlencode(
                {"url": pattern, "output": "json", "fl": "url"})
            time.sleep(2)
            meta = radar.request(base + "&showNumPages=true", timeout=60, parse=json.loads)
            if not isinstance(meta, dict) or type(meta.get("pages")) is not int or meta["pages"] < 0:
                raise ValueError("Common Crawl returned invalid page metadata")
            if not meta["pages"] and meta.get("blocks") != 0:
                raise ValueError("Common Crawl returned an unexplained empty index")
            query = {"crawl": crawl["id"], "pattern": pattern, "pages": meta["pages"]}
            for page in range(meta["pages"]):
                time.sleep(2)
                def parse(body):
                    records = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
                    if not records or not all(isinstance(r, dict) and isinstance(r.get("url"), str) for r in records):
                        raise ValueError("Common Crawl returned an empty or incomplete index page")
                    return records
                records = radar.request(base + "&page=" + str(page), timeout=120, parse=parse)
                (out / f"crawl-{len(completed):02d}-{page:03d}.json").write_text(json.dumps(records))
                for row in records:
                    try:
                        key = sources.board_key(row["url"])
                    except ValueError:
                        continue
                    if sources.provider(key) == source:
                        found.add(key)
            completed.append(query)
            print(f"[crawl] {crawl['id']} {pattern}: {meta['pages']} complete pages, {len(found)} candidates", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=PATTERNS, default="greenhouse")
    ap.add_argument("--common-crawl", action="store_true")
    ap.add_argument("--crawls", type=int, default=1)
    ap.add_argument("--pattern", action="append", help="override the provider's host patterns; repeatable")
    ap.add_argument("--athena", type=Path, action="append", help="CSV with a url column; repeatable")
    ap.add_argument("--seeds", type=Path, help="one board token, provider key or URL per line")
    ap.add_argument("--replay", type=Path, help="reuse a previous run's candidates.json without repeating discovery")
    ap.add_argument("--boards", type=Path, default=ROOT / "boards.json")
    ap.add_argument("--config", type=Path, default=ROOT / "config.json")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-checks", type=int, default=500)
    ap.add_argument("--add", action="store_true", help="add verified live boards only; existing state is retained")
    ap.add_argument("--out", type=Path, required=True, help="new evidence directory")
    args = ap.parse_args(argv)
    if not 1 <= args.workers <= 8 or not 1 <= args.crawls <= 10 or args.max_checks < 1:
        ap.error("workers must be 1–8, crawls 1–10, and max-checks positive")
    if not (args.common_crawl or args.athena or args.seeds or args.replay):
        ap.error("choose Common Crawl, Athena CSV, seeds or replay")
    cfg = radar.load_config(args.config)
    registry = radar.load_registry(args.boards)
    previously_known = set(registry)
    args.out.mkdir(parents=True, exist_ok=False)
    found, completed, errors = set(), [], []
    if args.replay:
        found.update(json.loads((args.replay / "candidates.json").read_text()))
    if args.seeds:
        for raw in args.seeds.read_text().splitlines():
            raw = raw.split("#", 1)[0].strip()
            if raw:
                found.add(sources.board_key(args.source + ":" + raw if ":" not in raw else raw))
    for path in args.athena or []:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            rows = csv.DictReader(stream)
            if "url" not in (rows.fieldnames or []):
                ap.error("Athena CSV needs a url column")
            for row in rows:
                try:
                    key = sources.board_key(row["url"])
                    if sources.provider(key) == args.source:
                        found.add(key)
                except ValueError:
                    continue
    if args.common_crawl:
        patterns = args.pattern or PATTERNS[args.source]
        if any(pattern not in PATTERNS[args.source] for pattern in patterns):
            ap.error("pattern must be one of this provider's known hosts")
        try:
            collect_crawl(args.source, args.crawls, patterns, args.out, found, completed)
        except Exception as error:
            errors.append({"stage": "crawl", "error": f"{type(error).__name__}: {error}"})
    found = {key for key in found if sources.provider(key) == args.source}
    radar.write_json(args.out / "candidates.json", sorted(found))
    unknown = sorted(found - set(registry))
    chosen = unknown[:args.max_checks]
    def check(key):
        try:
            if args.source == "greenhouse":
                jobs = sources.fetch_greenhouse(key, radar.request, lambda j: False)
            else:
                _, jobs, error = radar.fetch_board(key)
                if jobs is None:
                    return {"company": key, "status": "not found" if error == "HTTP 404" else "error", "error": error}
            return {"company": key, "status": "live", "jobs": len(jobs), "de": radar.has_germany(jobs, cfg)}
        except Exception as error:
            status = "not found" if getattr(error, "code", None) == 404 else "error"
            return {"company": key, "status": status, "error": f"{type(error).__name__}: {error}"}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(check, chosen))
    if args.add:
        registry = radar.load_registry(args.boards)
        for row in results:
            if row["status"] == "live":
                registry.setdefault(row["company"], {"de": row["de"]})
        radar.write_json(args.boards, registry, one_per_line=True)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "source": args.source,
              "candidates": len(found), "already_known": len(found & previously_known), "checked": len(results),
              "live": sum(r["status"] == "live" for r in results),
              "german_boards": sum(r.get("de", False) for r in results),
              "not_found": sum(r["status"] == "not found" for r in results),
              "deferred": max(0, len(unknown) - len(chosen)), "added": [r["company"] for r in results if r["status"] == "live"] if args.add else [],
              "completed_queries": completed, "errors": errors + [r for r in results if r["status"] == "error"]}
    radar.write_json(args.out / "run.json", report, indent=2)
    with (args.out / "boards.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["company", "status", "jobs", "de", "error"])
        writer.writeheader()
        writer.writerows(results)
    print(f"[backfill] {report['live']} live, {report['german_boards']} German boards; {len(report['errors'])} errors; {report['deferred']} deferred", flush=True)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
