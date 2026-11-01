#!/usr/bin/env python3
"""Refresh public Ashby and Greenhouse company profiles for the companies on the published board."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ashby_radar as radar
import sources

GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql"
CATALOG = "https://p1va.github.io/ashby-job-radar/companies.json"
QUERY = """query CompanyProfile($slug: String!) {
  organizationFromHostedJobsPageName(organizationHostedJobsPageName: $slug, searchContext: JobBoard) {
    organizationId name publicWebsite hostedJobsPageSlug customJobsPageUrl timezone
    theme { logoSquareImageUrl logoWordmarkImageUrl jobBoardTopDescriptionHtml }
  }
}"""
GREENHOUSE_BOARD = "https://boards-api.greenhouse.io/v1/boards/"
# The main host also serves EU-hosted boards; the EU host is only a fallback.
GREENHOUSE_PAGES = ("https://job-boards.greenhouse.io/", "https://job-boards.eu.greenhouse.io/")
GREENHOUSE_LOGO_HOST = re.compile(r"(?:s\d+-)?recruiting\.cdn\.greenhouse\.io")
GREENHOUSE_LOGO_PATH = "/external_greenhouse_job_boards/logos/"


class GraphQLError(ValueError):
    """A query-level failure should stop the batch rather than repeat for every slug."""


def public_url(value) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlsplit(value.strip())
    return value.strip() if parsed.scheme in ("http", "https") and parsed.hostname else ""


def parse_profile(body: bytes) -> dict:
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError("Ashby returned an invalid GraphQL response")
    if result.get("errors"):
        raise GraphQLError("Ashby returned GraphQL errors: " + str(result["errors"]))
    data = result.get("data") or {}
    org = data.get("organizationFromHostedJobsPageName") if isinstance(data, dict) else None
    if not isinstance(org, dict) or not org.get("organizationId") or not isinstance(org.get("name"), str) or not org["name"].strip():
        raise ValueError("Ashby returned no valid organization")
    theme = org.get("theme") or {}
    if not isinstance(theme, dict):
        raise ValueError("Ashby returned an invalid organization theme")
    return {"organization_id": org["organizationId"], "name": org["name"].strip(),
            "website": public_url(org.get("publicWebsite")),
            "careers_url": public_url(org.get("customJobsPageUrl")),
            "logo_url": public_url(theme.get("logoSquareImageUrl") or theme.get("logoWordmarkImageUrl")),
            "board_introduction_html": theme.get("jobBoardTopDescriptionHtml") or "",
            "canonical_slug": org.get("hostedJobsPageSlug") or "",
            "timezone": org.get("timezone") or "", "source": GRAPHQL}


def fetch_profile(slug: str) -> dict:
    body = json.dumps({"operationName": "CompanyProfile", "query": QUERY, "variables": {"slug": slug}}).encode()
    return radar.request(GRAPHQL, data=body, parse=parse_profile)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # urllib then raises the 3xx as an HTTPError


NO_REDIRECT = urllib.request.build_opener(_NoRedirect)


class _OpenGraphImage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.image = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("property") == "og:image" and not self.image:
            self.image = values.get("content") or ""


def board_logo(page: str) -> str:
    """Only a logo uploaded to the Greenhouse-hosted board, never another site's banner."""
    parser = _OpenGraphImage()
    parser.feed(page)
    url = html.unescape(parser.image.strip())
    parsed = urlsplit(url)
    if (parsed.scheme == "https" and GREENHOUSE_LOGO_HOST.fullmatch(parsed.hostname or "")
            and parsed.path.startswith(GREENHOUSE_LOGO_PATH)):
        return url
    return ""


def hosted_page(url: str) -> str:
    """GET without following redirects: a redirect means the company uses its own careers site."""
    request = urllib.request.Request(url, headers={**radar.UA, "Accept-Encoding": "gzip"})
    with NO_REDIRECT.open(request, timeout=30) as response:
        return radar.read_response(response).decode("utf-8", "replace")


def hosted_logo(token: str) -> str:
    for base in GREENHOUSE_PAGES:
        try:
            return board_logo(hosted_page(base + quote(token, safe="")))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            if 300 <= error.code < 400:
                return ""
            raise
    return ""


def parse_board(body: bytes) -> dict:
    board = json.loads(body)
    if not isinstance(board, dict) or not isinstance(board.get("name"), str) or not board["name"].strip():
        raise ValueError("Greenhouse returned no valid board")
    return {"name": board["name"].strip(),
            "board_introduction_html": board["content"] if isinstance(board.get("content"), str) else ""}


def fetch_greenhouse_profile(key: str) -> dict:
    token = key.split(":", 1)[1]
    board = radar.request(GREENHOUSE_BOARD + quote(token, safe=""), parse=parse_board)
    return {**board, "website": "", "careers_url": "", "logo_url": hosted_logo(token),
            "canonical_slug": token, "source": GREENHOUSE_BOARD}


def fetch_company(slug: str) -> dict:
    return fetch_greenhouse_profile(slug) if sources.provider(slug) == "greenhouse" else fetch_profile(slug)


def needs_refresh(profile: dict, now: datetime, max_age_days: int) -> bool:
    try:
        saved = datetime.fromisoformat(profile.get("fetched_at", "").replace("Z", "+00:00"))
        return not profile.get("name") or saved.tzinfo is None or not now - timedelta(days=max_age_days) < saved <= now
    except (TypeError, ValueError, AttributeError):
        return True


def refresh(slugs: list[str], cache: dict, cache_path: Path, *, max_age_days: int = 30,
            force: bool = False, delay: float = 1, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    report = {"started_at": now.isoformat(), "requested": len(slugs), "updated": [], "cached": [], "failed": [], "deferred": []}
    due = [slug for slug in slugs if force or needs_refresh(cache.get(slug, {}), now, max_age_days)]
    report["cached"] = [slug for slug in slugs if slug not in due]
    stopped, fetched = set(), 0
    for index, slug in enumerate(due):
        kind = sources.provider(slug)
        if kind in stopped:
            continue
        if fetched:
            time.sleep(delay)
        fetched += 1
        try:
            profile = {**fetch_company(slug), "fetched_at": now.isoformat()}
        except Exception as error:
            report["failed"].append({"company": slug, "error": str(error)})
            print(f"[companies] {slug}: {error}", file=sys.stderr, flush=True)
            if isinstance(error, GraphQLError) or (isinstance(error, urllib.error.HTTPError) and (error.code == 429 or error.code >= 500)):
                # Stop only this provider: a Greenhouse outage must not defer Ashby profiles.
                stopped.add(kind)
                report["deferred"] += [later for later in due[index + 1:] if sources.provider(later) == kind]
            continue
        cache[slug] = profile
        # Checkpoint each success; failures never erase the last good record.
        radar.write_json(cache_path, cache, indent=2)
        report["updated"].append(slug)
        print(f"[companies] {index + 1}/{len(due)} {slug}: {profile['name']}", flush=True)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=CATALOG, help="published companies.json URL, or local path")
    parser.add_argument("--cache", type=Path, default=ROOT / "company-info/companies.json")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--delay", type=float, default=1, help="seconds between companies")
    parser.add_argument("--force", action="store_true", help="refresh even recently cached companies")
    parser.add_argument("--board", action="append", help="only these slugs from the catalog; repeatable")
    args = parser.parse_args(argv)
    if args.max_age_days < 1 or args.delay < 0:
        parser.error("max-age-days must be positive and delay nonnegative")
    if public_url(args.catalog):
        catalog = radar.request(args.catalog, parse=json.loads)
    else:
        catalog = radar.load_json(Path(args.catalog), {})
    if not isinstance(catalog, dict) or not catalog or not all(isinstance(v, dict) for v in catalog.values()):
        parser.error("catalog must be a nonempty company lookup object")
    slugs = sorted(set(args.board) if args.board else catalog)
    if not set(slugs) <= set(catalog):
        parser.error("selected boards must be present in the catalog")
    # Workable job responses already carry company details.
    slugs = [slug for slug in slugs if sources.provider(slug) in ("ashby", "greenhouse")]
    cache = radar.load_json(args.cache, {})
    if not all(isinstance(v, dict) for v in cache.values()):
        parser.error("cache entries must be objects")
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    report = refresh(slugs, cache, args.cache, max_age_days=args.max_age_days, force=args.force, delay=args.delay)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    radar.write_json(args.report, report, indent=2)
    print(f"[companies] {len(report['updated'])} updated · {len(report['cached'])} cached · {len(report['failed'])} failed · {len(report['deferred'])} deferred")
    return 1 if report["failed"] or report["deferred"] else 0


if __name__ == "__main__":
    sys.exit(main())
