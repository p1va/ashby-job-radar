#!/usr/bin/env python3
"""
Add Ashby boards embedded on company careers pages to boards.json.

Reads the CSV that query.sql exports from Athena (one row per site). For each site it:
  1. reads the archived page straight from Common Crawl (one range request, no AWS needed);
  2. looks for the board slug: the embed script or ashbyBaseJobBoardUrl first, then any
     jobs.ashbyhq.com link, and finally a guess from the domain;
  3. confirms new slugs with the Ashby posting API.
Live boards that boards.json doesn't know yet are added as never checked ({"de": null}), so the
next radar run checks them once and the usual daily/weekly rotation takes over.

Standard library only. Usage:
  python embedded-boards/update_boards.py embedded-boards/results/CC-MAIN-2026-43.csv
  python embedded-boards/update_boards.py results/*.csv --dry-run     # report only
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "ashby-job-radar/1.0 (personal job digest)"}
# Pages often carry URLs JSON-escaped ("https:\/\/jobs.ashbyhq.com\/acme"), so allow "\/".
SLUG = rb"([^/\"'?#&<>\s\\]+)"
EMBED = [re.compile(rb"jobs\.ashbyhq\.com\\?/" + SLUG + rb"\\?/embed"),
         re.compile(rb"ashbyBaseJobBoardUrl\\?[\"']?\s*:\s*\\?[\"']https?:\\?/\\?/jobs\.ashbyhq\.com\\?/" + SLUG)]
LINK = re.compile(rb"jobs\.ashbyhq\.com\\?/" + SLUG)
NOT_SLUGS = {"api", "embed", "static", "favicon.ico"}


class Missing(Exception):
    pass


def get(url: str, headers=None, size=None, tries: int = 4) -> bytes:
    """GET with retries. With `size`, require a 206 of exactly that many bytes, so a server that
    ignores Range can't start a 1 GB download."""
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if size is None:
                    return r.read()
                body = r.read(size + 1)
                if r.status != 206 or len(body) != size:
                    raise ValueError(f"expected 206 with {size} bytes, got {r.status} with {len(body)}+")
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise Missing(url) from e
            if attempt == tries - 1:
                raise
        except OSError:
            if attempt == tries - 1:
                raise
        time.sleep(5 * (attempt + 1))      # Common Crawl answers bursts with 503 SlowDown
    raise RuntimeError(f"no response from {url}")


def archived_page(row: dict) -> bytes:
    """The captured response, already decompressed by Common Crawl (X-Crawler-Content-Encoding)."""
    start, size = int(row["warc_record_offset"]), int(row["warc_record_length"])
    url = "https://data.commoncrawl.org/" + row["warc_filename"]
    return gzip.decompress(get(url, {"Range": f"bytes={start}-{start + size - 1}"}, size))


def slugs(pattern, page: bytes) -> list[str]:
    found = {urllib.parse.unquote(m.decode(errors="replace")).strip().lower()
             for m in pattern.findall(page)}
    return sorted(s for s in found - NOT_SLUGS if "{" not in s)      # skip "${company_slug}" templates


def board_jobs(slug: str) -> int | None:
    """Number of jobs on a live board, None if Ashby has no such board."""
    try:
        body = get("https://api.ashbyhq.com/posting-api/job-board/" + urllib.parse.quote(slug))
    except Missing:
        return None
    return len(json.loads(body).get("jobs", []))


def candidates(row: dict) -> tuple[list[str], str]:
    """(slugs, source), strongest evidence first."""
    try:
        page = archived_page(row)
    except Exception as e:
        print(f"  {row['host']}: archive fetch failed ({e})", file=sys.stderr)
        page = b""
    embed = sorted({s for p in EMBED for s in slugs(p, page)})
    if embed:
        return embed, "embed"
    if links := slugs(LINK, page):
        return links, "link"
    stem, _, suffix = row["domain"].partition(".")
    return [stem, f"{stem}-{suffix.replace('.', '-')}"], "domain guess"


def write_registry(path: Path, reg: dict):
    """Same layout as the radar: one company per line, _meta first, written atomically."""
    rows = [f"{json.dumps(k)}: {json.dumps(v, sort_keys=True)}"
            for k, v in sorted(reg.items(), key=lambda item: (item[0] != "_meta", item[0]))]
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("{\n" + ",\n".join(rows) + "\n}\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("csv", nargs="+", type=Path, help="Athena results from query.sql")
    ap.add_argument("--boards", default=ROOT.parent / "boards.json", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="write the report only, not boards.json")
    args = ap.parse_args(argv)

    # The same site often appears in several crawls; keep its newest capture.
    newest = {}
    for path in args.csv:
        for row in csv.DictReader(open(path, encoding="utf-8")):
            if row["host"] not in newest or row["fetch_time"] > newest[row["host"]]["fetch_time"]:
                newest[row["host"]] = row
    sites = sorted(newest.values(), key=lambda r: r["host"])
    reg = json.loads(args.boards.read_text(encoding="utf-8"))
    known = {k.lower() for k in reg if k != "_meta"}

    report = []
    for i, row in enumerate(sites, 1):
        found, source = candidates(row)
        for slug in found:
            entry = {"host": row["host"], "slug": slug, "source": source, "sample_url": row["url"]}
            if slug in known:
                report.append({**entry, "status": "known"})
                continue
            try:
                jobs = board_jobs(slug)
            except Exception as e:
                report.append({**entry, "status": f"error: {e}"})
                continue
            if jobs is not None:
                report.append({**entry, "status": "new", "jobs": jobs})
            elif source != "domain guess":
                # A slug on the page that Ashby doesn't know is usually a leftover embed tag
                report.append({**entry, "status": "not found"})
            time.sleep(0.5)
        print(f"[{i}/{len(sites)}] {row['host']}: {', '.join(found) or '-'} ({source})", file=sys.stderr)
        time.sleep(2)

    out = args.csv[0].with_name("report.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, ["host", "slug", "source", "status", "jobs", "sample_url"])
        writer.writeheader()
        writer.writerows(report)
    new = sorted({r["slug"] for r in report if r["status"] == "new"})
    already = {r["slug"] for r in report if r["status"] == "known"}
    print(f"{len(sites)} sites · {len(new)} new boards · {len(already)} already in boards.json · report: {out}")
    if new and not args.dry_run:
        for slug in new:
            reg.setdefault(slug, {"de": None})
        write_registry(args.boards, reg)
        print(f"added {len(new)} boards to {args.boards} as never checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
