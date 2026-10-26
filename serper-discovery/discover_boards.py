#!/usr/bin/env python3
"""Discover live Ashby boards through Serper's Google search API. Stdlib, Python 3.9+."""
from __future__ import annotations

import argparse
import csv
import gzip
import html
import http.client
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERPER = "https://google.serper.dev/search"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/"
QUERIES = [
    'site:jobs.ashbyhq.com ("product manager" OR "GTM" OR "founder associate" '
    'OR "operations" OR "automation" OR "marketing") "Berlin"',
    'site:jobs.ashbyhq.com "Berlin"',
    'ashby Berlin',
]
RECENT = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
RESERVED = {"api", "embed", "static", "favicon.ico", "robots.txt", "sitemap.xml"}
JOB_ID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.I)
PAGE_LINK = re.compile(r"https?://jobs\.ashbyhq\.com/[^\s\"'<>\\]+", re.I)


def write_json(path: Path, data, compact: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if compact:
                rows = [f"{json.dumps(k)}: {json.dumps(v, sort_keys=True)}"
                        for k, v in sorted(data.items(), key=lambda item: (item[0] != "_meta", item[0]))]
                stream.write("{\n" + ",\n".join(rows) + "\n}\n")
            else:
                json.dump(data, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_registry(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        raise ValueError(f"Invalid registry: {path}")
    if len({key.lower() for key in data}) != len(data):
        raise ValueError(f"Case-only duplicate company IDs in {path}")
    return data


def request(url: str, *, data=None, headers=None, tries: int = 2, limit=None) -> bytes:
    """Paced by callers; retry only transient failures. Paid searches use tries=1."""
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "ashby-job-radar/1.0 (company discovery)",
        "Accept-Encoding": "gzip", **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                body = response.read() if limit is None else response.read(limit + 1)
                if limit is not None and len(body) > limit:
                    raise ValueError("Page exceeds download limit")
                if response.headers.get("Content-Encoding", "").strip().lower() == "gzip":
                    body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429) and not 500 <= error.code < 600:
                raise
            failure = error
        except (OSError, EOFError, ValueError, zlib.error, http.client.HTTPException) as error:
            failure = error
        if attempt + 1 == tries:
            raise failure
        delay = 5
        retry_after = (getattr(failure, "headers", None) or {}).get("Retry-After")
        if retry_after:
            try:
                delay = max(0, float(retry_after))
            except ValueError:
                try:
                    delay = max(0, (parsedate_to_datetime(retry_after)
                                    - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        if delay > 30:
            raise failure
        time.sleep(delay)


def ashby_link(url: str) -> tuple[str, str] | None:
    """Parse real Ashby URLs, preserving spaces and rejecting lookalike hosts."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except (TypeError, ValueError):
        return None
    if parsed.scheme not in ("https", "http") or parsed.hostname != "jobs.ashbyhq.com":
        return None
    parts = parsed.path.strip("/").split("/")
    slug = urllib.parse.unquote(parts[0]).strip().lower()
    if not slug or slug in RESERVED or any(c in slug for c in "/\\?#{}") or any(ord(c) < 32 for c in slug):
        return None
    candidate = parts[1] if len(parts) > 1 else ""
    return slug, candidate.lower() if JOB_ID.fullmatch(candidate) else ""


def result_links(result: dict):
    if not isinstance(result, dict):
        return
    if isinstance(result.get("link"), str):
        yield result["link"]
    sitelinks = result.get("sitelinks", [])
    if isinstance(sitelinks, dict):
        sitelinks = [item for group in sitelinks.values() if isinstance(group, list) for item in group]
    if not isinstance(sitelinks, list):
        return
    for item in sitelinks:
        if isinstance(item, dict) and isinstance(item.get("link"), str):
            yield item["link"]


def page_links(body: bytes) -> set[str]:
    text = html.unescape(body.decode("utf-8", errors="replace").replace(r"\/", "/"))
    return {url for url in PAGE_LINK.findall(text) if ashby_link(url)}


def board_info(data: dict) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("Ashby response has no jobs list")
    if not all(isinstance(job, dict) for job in data["jobs"]):
        raise ValueError("Ashby response contains an invalid job")
    jobs = [job for job in data["jobs"] if job.get("isListed") is not False]
    ids = set()
    for job in jobs:
        if job.get("id"):
            ids.add(str(job["id"]).lower())
        link = ashby_link(job.get("jobUrl", ""))
        if link and link[1]:
            ids.add(link[1])
    berlin = sum("berlin" in json.dumps({key: job.get(key) for key in
                 ("location", "address", "secondaryLocations")}).lower() for job in jobs)
    return {"jobs": len(jobs), "berlin_jobs": berlin, "job_ids": ids}


def add_boards(path: Path, slugs: list[str]) -> list[str]:
    # Re-read at write time so unrelated changes made during discovery are retained.
    registry = load_registry(path)
    known = {slug.lower() for slug in registry}
    added = sorted({slug.lower() for slug in slugs} - known)
    if added:
        registry.update({slug: {"de": None} for slug in added})
        write_json(path, registry, compact=True)
    return added


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key, "") for key in fields} for row in rows)


def positive(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 10:
        raise argparse.ArgumentTypeError("must be between 1 and 10")
    return number


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", action="append", help="Google query; repeat to override the three defaults")
    ap.add_argument("--pages", type=positive, default=2, help="maximum pages per query (default: 2, ten results each)")
    ap.add_argument("--country", default="de", help="Serper gl (default: de)")
    ap.add_argument("--language", default="en", help="Serper hl (default: en)")
    ap.add_argument("--recent", choices=RECENT, help="Google date filter; does not prove a job is still open")
    ap.add_argument("--follow-sites", type=int, default=0, metavar="N",
                    help="inspect up to N external result pages for Ashby links (default: 0)")
    ap.add_argument("--boards", type=Path, default=ROOT / "boards.json")
    ap.add_argument("--add", action="store_true", help="merge verified live boards into --boards (default: report only)")
    ap.add_argument("--replay", type=Path, help="reuse a previous run's search results without Serper calls")
    ap.add_argument("--out", type=Path, help="new output directory; defaults to data/exploration/serper-<UTC time>")
    args = ap.parse_args(argv)
    if args.follow_sites < 0:
        ap.error("--follow-sites must be nonnegative")
    if args.replay and (args.query or args.recent):
        ap.error("--replay uses the saved queries and date filters")
    key = os.environ.get("SERPER_API_KEY", "")
    if not args.replay and not key:
        ap.error("Set SERPER_API_KEY in your environment")
    try:
        known = {slug.lower() for slug in load_registry(args.boards) if slug != "_meta"}
    except (ValueError, OSError) as error:
        ap.error(str(error))
    out = args.out or ROOT / "data/exploration" / datetime.now(timezone.utc).strftime("serper-%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    errors, searches, hits, boards = [], [], [], {}
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "registry": str(args.boards),
               "known_companies": len(known), "serper_requests": 0, "credits_reported": 0,
               "replay": str(args.replay) if args.replay else None, "errors": errors}

    def error_text(error):
        text = f"{type(error).__name__}: {error}"
        return text.replace(key, "[redacted]") if key else text

    if args.replay:
        for path in sorted((args.replay / "searches").glob("*.json")):
            saved = json.loads(path.read_text(encoding="utf-8"))
            if "response" in saved:
                searches.append(saved)
        if not searches:
            ap.error("No successful search responses in --replay directory")
    else:
        stop = False
        for query in list(dict.fromkeys(args.query or QUERIES)):
            seen_urls = set()
            for page in range(1, args.pages + 1):
                payload = {"q": query, "gl": args.country, "hl": args.language, "num": 10, "page": page}
                if args.recent:
                    payload["tbs"] = RECENT[args.recent]
                entry = {"request": payload}
                if summary["serper_requests"]:
                    time.sleep(1)
                summary["serper_requests"] += 1
                try:
                    body = request(SERPER, data=json.dumps(payload).encode(), tries=1,
                                   headers={"X-API-KEY": key, "Content-Type": "application/json"})
                    response = json.loads(body)
                    if not isinstance(response, dict) or not isinstance(response.get("organic", []), list):
                        raise ValueError("Invalid Serper response")
                    if response.get("message") or response.get("error"):
                        raise ValueError("Serper returned an error; inspect the account dashboard")
                    entry["response"] = response
                    summary["credits_reported"] += response.get("credits", 0)
                    urls = {url for item in response.get("organic", []) for url in result_links(item)}
                    searches.append(entry)
                    print(f"[search] page {page}: {len(response.get('organic', []))} results · {query}", flush=True)
                except Exception as error:
                    entry["error"] = error_text(error)
                    errors.append({"stage": "search", "query": query, "page": page, "error": entry["error"]})
                    stop = True      # no repeated paid calls after an account/network failure
                write_json(out / "searches" / f"{summary['serper_requests']:03d}.json", entry)
                if stop or not urls or urls <= seen_urls:
                    break
                seen_urls.update(urls)
            if stop:
                break

    for i, entry in enumerate(searches, 1):
        if args.replay:
            write_json(out / "searches" / f"{i:03d}.json", entry)
        for result in entry["response"].get("organic", []):
            for url in result_links(result):
                hits.append({"query": entry["request"]["q"], "page": entry["request"].get("page", 1),
                             "position": result.get("position", ""), "title": result.get("title", ""),
                             "url": url, "source": "search"})

    followed = {}
    for hit in list(hits):
        if not args.follow_sites:
            break
        if ashby_link(hit["url"]):
            continue
        url = hit["url"]
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            errors.append({"stage": "page", "url": url, "error": "Invalid result URL"})
            continue
        if url not in followed and len(followed) < args.follow_sites and parsed.scheme in ("https", "http"):
            followed[url] = set()
            try:
                body = request(url, limit=2 * 1024 * 1024)
                followed[url] = page_links(body)
                page_file = out / "pages" / f"{len(followed):03d}.html"
                page_file.parent.mkdir(exist_ok=True)
                page_file.write_bytes(body)
            except Exception as error:
                errors.append({"stage": "page", "url": url, "error": error_text(error)})
            time.sleep(1)
        for link in sorted(followed.get(url, [])):
            hits.append({**hit, "url": link, "source": url})

    for hit in hits:
        parsed = ashby_link(hit["url"])
        if parsed:
            hit["slug"], hit["job_id"] = parsed
            boards.setdefault(parsed[0], {"slug": parsed[0], "known": parsed[0] in known, "hits": []})["hits"].append(hit)

    for slug, board in sorted(boards.items()):
        try:
            data = json.loads(request(ASHBY + urllib.parse.quote(slug, safe="")))
            info = board_info(data)
            write_json(out / "boards" / (urllib.parse.quote(slug, safe="") + ".json"), data)
            board.update({k: v for k, v in info.items() if k != "job_ids"}, status="live")
            for hit in board["hits"]:
                hit["job_status"] = ("currently listed" if hit["job_id"] in info["job_ids"]
                                     else "not currently listed") if hit["job_id"] else "board link"
        except urllib.error.HTTPError as error:
            board.update(status="not found" if error.code == 404 else "error", error=error_text(error))
        except Exception as error:
            board.update(status="error", error=error_text(error))
        if board["status"] == "error":
            errors.append({"stage": "board", "slug": slug, "error": board["error"]})
        print(f"[board] {slug}: {board['status']} · {board.get('jobs', '?')} listed jobs", flush=True)
        time.sleep(0.5)

    live = sorted(slug for slug, board in boards.items() if board["status"] == "live")
    new = sorted(set(live) - known)
    summary.update(search_pages=len(searches), search_results=sum(len(s['response'].get('organic', [])) for s in searches),
                   result_links=len(hits), candidate_boards=len(boards), live_boards=len(live),
                   new_live_boards=len(new), new_slugs=new, external_pages=len(followed), added=[])
    if args.add:
        try:
            summary["added"] = add_boards(args.boards, live)
        except Exception as error:
            errors.append({"stage": "registry", "error": error_text(error)})
    rows = []
    for board in boards.values():
        rows.append({**{k: v for k, v in board.items() if k != "hits"},
                     "search_hits": len(board["hits"]),
                     "current_indexed_jobs": len({h['job_id'] for h in board['hits'] if h.get('job_status') == 'currently listed'}),
                     "stale_indexed_jobs": len({h['job_id'] for h in board['hits'] if h.get('job_status') == 'not currently listed'}),
                     "queries": json.dumps(sorted({h['query'] for h in board['hits']}))})
    write_csv(out / "boards.csv", sorted(rows, key=lambda r: (r["known"], r["slug"])),
              ["slug", "status", "known", "jobs", "berlin_jobs", "search_hits", "current_indexed_jobs", "stale_indexed_jobs", "queries", "error"])
    write_csv(out / "search-results.csv", hits,
              ["query", "page", "position", "title", "url", "source", "slug", "job_id", "job_status"])
    (out / "new-slugs.txt").write_text("".join(slug + "\n" for slug in new), encoding="utf-8")
    (out / "live-slugs.txt").write_text("".join(slug + "\n" for slug in live), encoding="utf-8")
    write_json(out / "run.json", summary)
    print(f"{len(live)} live boards · {len(new)} new · {len(errors)} errors · report: {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
