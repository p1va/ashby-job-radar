#!/usr/bin/env python3
"""Refresh public Ashby company profiles for the companies on the published board."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
import urllib.error
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ashby_radar as radar

GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql"
CATALOG = "https://p1va.github.io/ashby-job-radar/companies.json"
QUERY = """query CompanyProfile($slug: String!) {
  organizationFromHostedJobsPageName(organizationHostedJobsPageName: $slug, searchContext: JobBoard) {
    organizationId name publicWebsite hostedJobsPageSlug customJobsPageUrl timezone
    theme { logoSquareImageUrl logoWordmarkImageUrl jobBoardTopDescriptionHtml }
  }
}"""


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
    for index, slug in enumerate(due):
        if index:
            time.sleep(delay)
        try:
            profile = {**fetch_profile(slug), "fetched_at": now.isoformat()}
        except Exception as error:
            report["failed"].append({"company": slug, "error": str(error)})
            print(f"[companies] {slug}: {error}", file=sys.stderr, flush=True)
            if isinstance(error, GraphQLError) or (isinstance(error, urllib.error.HTTPError) and (error.code == 429 or error.code >= 500)):
                report["deferred"] = due[index + 1:]
                break
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
