#!/usr/bin/env python3
"""
Ashby Job Radar — daily digest of recently published growth, product, marketing,
operations, GTM and BD roles anywhere in Germany, pulled from Ashby's public job-board API.

Ashby has no global search, so this script:
  1. reads company board slugs from companies.txt (jobs.ashbyhq.com/<slug>)
  2. optionally discovers new slugs via a web-search API (Brave), running
     the query  site:jobs.ashbyhq.com ("growth" OR "product") berlin|munich
  3. fetches every board, filters by title + location + recency
  4. skips anything already sent before (seen.json)
  5. writes digest.md / digest.html and emails it if SMTP is configured

Standard library only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import gzip
import html
import http.client
import json
import os
import re
import smtplib
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
UA = {"User-Agent": "ashby-job-radar/1.0 (personal job digest)"}


# ---------------------------------------------------------------- config / io
def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_slugs(path: Path) -> list[str]:
    """Accepts bare slugs or full jobs.ashbyhq.com URLs, one per line; # comments."""
    slugs = []
    if not path.exists():
        return slugs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.search(r"jobs\.ashbyhq\.com/([^/?#\s]+)", line)
        slug = m.group(1) if m else line
        slug = urllib.parse.unquote(slug).strip()
        if slug and slug.lower() not in {s.lower() for s in slugs}:
            slugs.append(slug)
    return slugs


def _notion_text(prop: dict) -> str:
    t = prop.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in prop.get(t, []))
    if t == "url":
        return prop.get("url") or ""
    if t == "select":
        return (prop.get("select") or {}).get("name", "")
    if t == "formula":
        return str((prop.get("formula") or {}).get("string") or "")
    return ""


def load_slugs_notion(cfg: dict) -> list[str] | None:
    """Read Ashby IDs from a Notion database (NOTION_TOKEN + NOTION_DATABASE_ID).
    Column name from config 'notion.ashby_id_property' (text, title or URL column).
    If 'notion.active_property' names a checkbox column, only ticked rows are used."""
    token, db = os.environ.get("NOTION_TOKEN"), os.environ.get("NOTION_DATABASE_ID")
    if not (token and db):
        return None
    ncfg = cfg.get("notion", {})
    id_prop = ncfg.get("ashby_id_property", "Ashby ID")
    active = ncfg.get("active_property")
    db = re.sub(r"[^0-9a-fA-F]", "", db.split("?")[0])[-32:]   # accept a full Notion URL
    body: dict = {"page_size": 100}
    if active:
        body["filter"] = {"property": active, "checkbox": {"equals": True}}
    slugs, cursor = [], None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{db}/query",
            data=json.dumps(body).encode(), method="POST", headers={
                "Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
                "Content-Type": "application/json", **UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        for page in data.get("results", []):
            prop = page.get("properties", {}).get(id_prop)
            if not prop:
                continue
            raw = _notion_text(prop).strip()
            m = re.search(r"jobs\.ashbyhq\.com/([^/?#\s]+)", raw)
            slug = urllib.parse.unquote(m.group(1) if m else raw).strip()
            if slug and slug.lower() not in {s.lower() for s in slugs}:
                slugs.append(slug)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return slugs


def load_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"Cannot read {path}; repair or restore this state file before running again") from e
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}; existing state was not replaced")
    return data


def load_registry(path: Path) -> dict:
    data = load_json(path, {})
    registry = {slug if slug == "_meta" else slug.lower(): state for slug, state in data.items()}
    if len(registry) != len(data):
        raise ValueError(f"Duplicate company IDs differing only by case in {path}; merge them before running again")
    return registry


def write_json(path: Path, data: dict, indent: int = 1, one_per_line: bool = False):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if one_per_line:
                rows = [f"{json.dumps(key)}: {json.dumps(value, sort_keys=True)}"
                        for key, value in sorted(data.items(), key=lambda item: (item[0] != "_meta", item[0]))]
                f.write("{\n" + ",\n".join(rows) + "\n}\n")
            else:
                json.dump(data, f, indent=indent, sort_keys=True)
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


# ---------------------------------------------------------------- discovery
SLUG_RE = re.compile(r"jobs\.ashbyhq\.com/([^/?#\s\"']+)")


def discover_tavily(cfg: dict, key: str) -> set[str]:
    """Tavily Search API — free tier, 1,000 searches/month, no card."""
    found = set()
    for q in cfg.get("discovery_queries", []):
        q = q.replace("site:jobs.ashbyhq.com", "").strip()   # domain filter does this
        body = json.dumps({"query": q, "include_domains": ["jobs.ashbyhq.com"],
                           "max_results": 20, "time_range": "week"}).encode()
        req = urllib.request.Request("https://api.tavily.com/search", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}", **UA})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
        except Exception as e:
            print(f"[discover] tavily query failed: {q!r}: {e}", file=sys.stderr)
            continue
        for item in data.get("results", []):
            m = SLUG_RE.search(item.get("url", ""))
            if m:
                found.add(urllib.parse.unquote(m.group(1)))
    return found


def discover_slugs(cfg: dict) -> set[str]:
    """Optional: find new Ashby boards via Tavily (TAVILY_API_KEY) or Brave (BRAVE_API_KEY)."""
    if os.environ.get("TAVILY_API_KEY"):
        return discover_tavily(cfg, os.environ["TAVILY_API_KEY"])
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        return set()
    found = set()
    for q in cfg.get("discovery_queries", []):
        url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
            {"q": q, "count": 20, "freshness": "pw"})
        req = urllib.request.Request(url, headers={
            "Accept": "application/json", "X-Subscription-Token": key, **UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
        except Exception as e:  # discovery is best-effort
            print(f"[discover] query failed: {q!r}: {e}", file=sys.stderr)
            continue
        for item in data.get("web", {}).get("results", []):
            m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", item.get("url", ""))
            if m:
                found.add(urllib.parse.unquote(m.group(1)))
    return found


# ---------------------------------------------------------------- crawl harvest
CC_INDEX = "https://index.commoncrawl.org"
NOT_SLUGS = {"api", "embed", "static", "favicon.ico", "robots.txt", "sitemap.xml", ""}


def read_response(response) -> bytes:
    body = response.read()
    if response.headers.get("Content-Encoding", "").strip().lower() == "gzip":
        body = gzip.decompress(body)
    return body


def request(url: str, timeout: int = 30, tries: int = 2, parse=None):
    """GET with gzip and bounded retries, including validation of the full body."""
    for attempt in range(tries):
        try:
            headers = {**UA, "Accept-Encoding": "gzip"}
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                body = read_response(r)
            return parse(body) if parse else body
        except urllib.error.HTTPError as e:
            if e.code not in (408, 429) and not 500 <= e.code < 600:
                raise
            err = e
        except (OSError, EOFError, ValueError, zlib.error, http.client.HTTPException) as e:
            err = e
        if attempt + 1 == tries:
            raise err
        delay = 5 * (attempt + 1)
        retry_after = (getattr(err, "headers", None) or {}).get("Retry-After")
        if retry_after:
            try:
                delay = max(0, float(retry_after))
            except ValueError:
                try:
                    delay = max(0, (parsedate_to_datetime(retry_after)
                                    - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        if delay > 60:       # leave a long server-requested pause for the next run
            raise err
        time.sleep(delay)


def _get(url: str, timeout: int = 60, tries: int = 2, parse=None):
    try:
        return request(url, timeout=timeout, tries=tries, parse=parse)
    except Exception as e:
        print(f"[harvest] giving up on {url}: {e}", file=sys.stderr)
        return None


def harvest_common_crawl(n_crawls: int, completed=()) -> tuple[set[str], bool, set[str]]:
    """Return found slugs, completion, and crawl IDs whose pages were all valid."""
    finished = set(completed)
    def parse_crawls(body):
        data = json.loads(body)
        if not isinstance(data, list) or not data:
            raise ValueError("missing crawl list")
        if not all(isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"] for c in data):
            raise ValueError("invalid crawl list")
        return [c["id"] for c in data[:n_crawls]]

    def parse_pages(body):
        data = json.loads(body)
        if not isinstance(data, dict) or type(data.get("pages")) is not int or data["pages"] < 0:
            raise ValueError("invalid page count")
        if "blocks" in data and (type(data["blocks"]) is not int or data["blocks"] < 0):
            raise ValueError("invalid block count")
        if data.get("blocks", 0) > 0 and data["pages"] == 0:
            raise ValueError("nonempty index has no pages")
        return data

    def parse_page(body):
        found = set()
        lines = [line for line in body.decode("utf-8").splitlines() if line.strip()]
        if not lines and info.get("blocks") != 0:
            raise ValueError("empty index page")
        for line in lines:
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("url"), str) or not record["url"]:
                raise ValueError("index record has no URL")
            m = re.match(r"https?://jobs\.ashbyhq\.com/([^/?#]+)", record["url"], re.I)
            if m:
                slug = urllib.parse.unquote(m.group(1)).strip()
                if slug.lower() not in NOT_SLUGS and "." not in slug:
                    found.add(slug)
        return found

    crawls = _get(f"{CC_INDEX}/collinfo.json", timeout=60, parse=parse_crawls)
    if crawls is None:
        return set(), False, finished
    slugs: set[str] = set()
    complete = True
    for cid in crawls:
        if cid in finished:
            print(f"[harvest] {cid}: already complete", file=sys.stderr)
            continue
        base = f"{CC_INDEX}/{cid}-index?url=jobs.ashbyhq.com/*&output=json&fl=url"
        time.sleep(2)        # keep Common Crawl requests serial and paced
        info = _get(base + "&showNumPages=true", timeout=60, parse=parse_pages)
        if info is None:
            complete = False
            continue
        pages = info["pages"]
        before = len(slugs)
        valid = 0
        for p in range(pages):
            time.sleep(2)
            found = _get(f"{base}&page={p}", parse=parse_page)
            if found is None:
                complete = False
                continue
            slugs.update(found)
            valid += 1
        print(f"[harvest] {cid}: {valid}/{pages} valid pages, +{len(slugs) - before} companies",
              file=sys.stderr)
        if valid == pages:
            finished.add(cid)
    return slugs, complete, finished


def update_registry(reg: dict, cfg: dict, now: datetime) -> int:
    """Refresh the list of all known Ashby companies from Common Crawl once a week."""
    hcfg = cfg.get("harvest", {})
    if not hcfg.get("enabled", True):
        return 0
    last = reg.get("_meta", {}).get("harvested")
    if last and (now.date() - datetime.fromisoformat(last).date()).days < hcfg.get("every_days", 7):
        return 0
    completed = reg.get("_meta", {}).get("completed_crawls", [])
    found, complete, finished = harvest_common_crawl(hcfg.get("crawls", 3), completed)
    known = {k.lower() for k in reg if k != "_meta"}
    added = 0
    for s in found:
        s = s.lower()
        if s.lower() not in known:
            reg[s] = {"de": None}
            known.add(s.lower())
            added += 1
    if finished:
        reg.setdefault("_meta", {})["completed_crawls"] = sorted(finished)
    reg.setdefault("_meta", {})["harvest_incomplete"] = not complete
    if complete:
        reg.setdefault("_meta", {})["harvested"] = now.date().isoformat()
    else:
        print("[harvest] incomplete; keeping previous harvest date to retry next run", file=sys.stderr)
    return added


def pick_registry_boards(reg: dict, now: datetime, cfg: dict) -> list[str]:
    """German companies every day; never-checked ones up to a cap; the rest on a 7-day rotation."""
    import zlib
    cap = cfg.get("harvest", {}).get("max_new_checks_per_run", 3000)
    out, new = [], 0
    for slug, st in reg.items():
        if slug == "_meta":
            continue
        if st.get("de"):
            out.append(slug)
        elif st.get("de") is None:
            if new < cap:
                out.append(slug)
                new += 1
        elif zlib.crc32(slug.lower().encode()) % 7 == now.weekday():
            out.append(slug)
    return out


def has_germany(jobs: list[dict], cfg: dict) -> bool:
    return any(match_location(j, cfg) for j in jobs)


# ---------------------------------------------------------------- fetching
def fetch_board(slug: str) -> tuple[str, list[dict] | None, str | None]:
    def parse(body):
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError("response has no jobs list")
        if not all(isinstance(job, dict) for job in data["jobs"]):
            raise ValueError("response contains an invalid job")
        return data["jobs"]

    try:
        jobs = request(API.format(slug=urllib.parse.quote(slug, safe="")), parse=parse)
        return slug, jobs, None
    except urllib.error.HTTPError as e:
        return slug, None, f"HTTP {e.code}"
    except Exception as e:
        return slug, None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- filtering
def norm(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("ü", "u"), ("ö", "o"), ("ä", "a"), ("ß", "ss"), ("’", ""), ("'", ""), ("–", "-")):
        s = s.replace(a, b)
    return s


def job_locations(job: dict) -> list[str]:
    locs = []
    for location in [job] + (job.get("secondaryLocations") or []):
        locs.append(location.get("location") or "")
        addr = (location.get("address") or {}).get("postalAddress") or {}
        locs.append(" ".join(str(addr.get(k) or "") for k in
                             ("addressLocality", "addressRegion", "addressCountry")))
    return [l for l in locs if l.strip()]


def match_location(job: dict, cfg: dict) -> str | None:
    """Return a display label like 'Berlin', 'Berlin / Munich' or 'Germany', or None."""
    blob = norm(" | ".join(job_locations(job)))
    cities = []
    for city in cfg["cities"]:
        if any(re.search(r"\b" + re.escape(norm(a)) + r"\b", blob) for a in city["aliases"]):
            cities.append(city["label"])
    if cities:
        return " / ".join(cities)
    countries = {norm(str(((location.get("address") or {}).get("postalAddress") or {})
                         .get("addressCountry") or ""))
                 for location in [job] + (job.get("secondaryLocations") or [])}
    if countries.intersection({"de", "deu", "germany", "deutschland"}) or any(
            re.search(r"\b" + re.escape(norm(t)) + r"\b", blob) for t in cfg["country_terms"]):
        return "Germany"
    if cfg.get("include_remote_europe") and any(
            re.search(r"\b" + t + r"\b", blob) for t in ("europe", "emea", "eu")):
        return "Remote Europe"
    return None


def work_style(job: dict) -> str:
    wt = norm(job.get("workplaceType") or "")
    if wt == "remote":
        return "Remote"
    if wt == "hybrid":
        return "Hybrid"
    if wt in ("onsite", "on-site", "on site"):
        return "Office"
    locs = norm(" ".join(job_locations(job)))
    if "hybrid" in locs:
        return "Hybrid"
    if job.get("isRemote") or "remote" in locs:
        return "Remote"
    return "Not stated"


def word_hit(text: str, words: list[str]) -> list[str]:
    t = norm(text)
    return [w for w in words if re.search(r"\b" + re.escape(norm(w)) + r"\b", t)]


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


GERMAN_STOPWORDS = {"und", "die", "der", "das", "wir", "mit", "fur", "bei", "ist", "sie",
                    "ein", "eine", "auf", "zu", "von", "den", "dich", "du", "uns", "ihre"}


def german_required(job: dict, cfg: dict) -> str | None:
    desc = norm(job.get("descriptionPlain") or "")
    if not desc:
        return None
    words = re.findall(r"[a-z]+", desc)
    if words and sum(w in GERMAN_STOPWORDS for w in words) / len(words) > 0.08:
        return "German required (ad is in German)"
    if any(norm(p) in desc for p in cfg["german_required_phrases"]):
        return "German required"
    strong = r"(fluent|fluency|native|c1|c2|business|proficien|required|must|excellent|professional|verhandlungssicher)"
    soft = r"(plus|nice to have|nice-to-have|bonus|advantage|beneficial|preferred|helpful|not required)"
    for m in re.finditer(r"\b(german|deutsch)\b", desc):
        window = desc[max(0, m.start() - 60): m.end() + 60]
        if re.search(strong, window) and not re.search(soft, window):
            return "German required"
    return None


def compensation(job: dict) -> str:
    comp = job.get("compensation") or {}
    return (comp.get("compensationTierSummary")
            or comp.get("scrapeableCompensationSalarySummary") or "")


def in_focus(loc: str, style: str, cfg: dict) -> bool:
    """Keep only the cities you care about (+ remote roles in Germany)."""
    focus = cfg.get("focus_cities")
    if not focus:
        return True
    if any(c in focus for c in loc.split(" / ")):
        return True
    return bool(cfg.get("include_remote_germany", True)) and loc == "Germany" and style == "Remote"


def publish_cutoff(seen: dict, cfg: dict, now: datetime, board=None) -> datetime:
    """new_only: recover unsent board coverage within sent-history retention.
    week: jobs published in the last lookback_days."""
    if cfg.get("digest_mode", "new_only") == "new_only":
        last = parse_date(seen.get("_last_run"))
        buffer = timedelta(hours=cfg.get("overlap_hours", 3))
        cutoff = (last - buffer) if last else now - timedelta(hours=24)
        if board:
            since = parse_date(board.get("eligible_since"))
            if since is None:
                checked = parse_date(board.get("checked"))
                since = checked - buffer if checked else None
            if since:
                cutoff = min(cutoff, since)
        return max(cutoff, now - timedelta(days=cfg.get("forget_after_days", 60)))
    return now - timedelta(days=cfg["lookback_days"])


def curate(boards: dict[str, list[dict]], cfg: dict, seen: dict, now: datetime, registry=None):
    states = {slug.lower(): st for slug, st in (registry or {}).items() if slug != "_meta"}
    picks = []
    for slug, jobs in boards.items():
        cutoff = publish_cutoff(seen, cfg, now, states.get(slug.lower()))
        for job in jobs:
            if job.get("isListed") is False:
                continue
            title = job.get("title", "")
            if not word_hit(title, cfg["title_include"]):
                continue
            if word_hit(title, cfg["title_exclude"]):
                continue
            loc = match_location(job, cfg)
            if not loc:
                continue
            pub = parse_date(job.get("publishedAt"))
            if not pub or pub < cutoff:
                continue
            key = job.get("id") or job.get("jobUrl")
            if key in seen and cfg.get("digest_mode", "new_only") == "new_only":
                continue
            job["_new"] = key not in seen
            job["_slug"], job["_loc"], job["_pub"] = slug, loc, pub
            job["_style"] = work_style(job)
            if not in_focus(loc, job["_style"], cfg):
                continue
            job["_german"] = german_required(job, cfg)
            job["_comp"] = compensation(job)
            picks.append(job)
    picks.sort(key=lambda j: j["_pub"], reverse=True)       # newest release first
    return picks


# ---------------------------------------------------------------- output
def company_name(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def job_meta(j: dict) -> list[str]:
    return [f"📍 {j['_loc']}", j["_style"], f"💶 {j['_comp'] or 'Salary not listed'}",
            f"posted {j['_pub']:%d %b}"]


def render_md(picks, now, stats) -> str:
    lines = [f"# Ashby Job Radar — {now:%a %d %b %Y}", "",
             (f"{len(picks)} new roles · newest first · "
              if stats["mode"] == "new_only" else
              f"{len(picks)} roles in Germany posted in the last {stats['days']} days "
              f"({stats['n_new']} 🆕 since last email) · newest first · ") +
             f"{stats['boards_ok']} companies checked", ""]
    if stats.get("boards_failed"):
        lines += [f"_Company boards unavailable: {stats['boards_failed']}. Results may be incomplete._", ""]
    if not picks:
        lines.append("_Nothing new today._" if stats["boards_ok"] else
                     "_No boards could be fetched; new jobs could not be checked._")
    for j in picks:
        new = "🆕 " if j.get("_new") and stats["mode"] == "week" else ""
        lines.append(f"### {new}[{j['title']}]({j['jobUrl']}) — {company_name(j['_slug'])}")
        lines.append(" · ".join(job_meta(j)))
        if j["_german"]:
            lines.append(f"🇩🇪 {j['_german']}")
        lines.append("")
    if stats["bad_slugs"]:
        lines += ["---", "Boards that failed (fix or remove the Ashby ID): "
                  + ", ".join(f"`{s}` ({e})" for s, e in stats["bad_slugs"])]
    if stats["new_slugs"]:
        label = "New boards discovered" if stats.get("dry_run") else "New boards discovered and added"
        lines += ["", label + ": "
                  + ", ".join(f"`{s}`" for s in sorted(stats["new_slugs"]))]
    return "\n".join(lines) + "\n"


STYLE_COLOR = {"Remote": "#047857", "Hybrid": "#1d4ed8", "Office": "#6b7280", "Not stated": "#9ca3af"}


def render_html(picks, now, stats) -> str:
    e = html.escape
    rows = []
    for j in picks:
        pill = (f'<span style="display:inline-block;padding:1px 8px;border-radius:10px;'
                f'border:1px solid {STYLE_COLOR[j["_style"]]};color:{STYLE_COLOR[j["_style"]]};'
                f'font-size:12px">{e(j["_style"])}</span>')
        german = (f'<div style="color:#b45309;font-size:13px;margin-top:2px">🇩🇪 {e(j["_german"])}</div>'
                  if j["_german"] else "")
        rows.append(
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #e5e7eb">'
            + ('<span style="background:#dcfce7;color:#166534;font-size:11px;font-weight:600;'
               'padding:1px 6px;border-radius:4px;margin-right:6px">NEW</span>'
               if j.get("_new") and stats["mode"] == "week" else "")
            + f'<a href="{e(j["jobUrl"])}" style="font-weight:600;color:#1d4ed8;'
            f'text-decoration:none">{e(j["title"])}</a>'
            f'<div style="color:#111">{e(company_name(j["_slug"]))}</div>'
            f'<div style="color:#374151;font-size:13px;margin-top:2px">📍 {e(j["_loc"])} &nbsp;{pill}'
            f' &nbsp;💶 {e(j["_comp"] or "Salary not listed")}</div>'
            f'<div style="color:#9ca3af;font-size:12px">posted {j["_pub"]:%a %d %b}</div>'
            f'{german}</td></tr>')
    empty = "Nothing new today." if stats["boards_ok"] else "No boards could be fetched; new jobs could not be checked."
    body = "".join(rows) or f'<tr><td style="color:#6b7280">{empty}</td></tr>'
    foot = ""
    if stats["bad_slugs"]:
        foot = ('<p style="color:#9ca3af;font-size:12px">Boards that failed: '
                + e(", ".join(s for s, _ in stats["bad_slugs"])) + "</p>")
    warning = (f'<p style="color:#6b7280">Company boards unavailable: {stats["boards_failed"]}. '
               'Results may be incomplete.</p>') if stats.get("boards_failed") else ""
    return (f'<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:640px">'
            f'<h2 style="margin:0">Ashby Job Radar</h2>'
            f'<p style="color:#6b7280;margin:4px 0 12px">{now:%A %d %B %Y} · '
            + (f'{len(picks)} new roles'
               if stats["mode"] == "new_only" else
               f'{len(picks)} roles in Germany from the last {stats["days"]} days · '
               f'{stats["n_new"]} new since last email')
            + f' · {stats["boards_ok"]} companies checked</p>'
            f'{warning}<table style="width:100%;border-collapse:collapse">{body}</table>{foot}</div>')


def send_email(subject: str, html_body: str, text_body: str) -> bool:
    user, pw, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASS", "EMAIL_TO"))
    if not (user and pw and to):
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, pw)
        refused = s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    return True


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--config", default=ROOT / "config.json", type=Path)
    ap.add_argument("--companies", default=ROOT / "companies.txt", type=Path)
    ap.add_argument("--seen", default=ROOT / "seen.json", type=Path)
    ap.add_argument("--registry", default=ROOT / "boards.json", type=Path,
                    help="all Ashby companies found by crawling, with a Germany flag")
    ap.add_argument("--out", default=ROOT / "output", type=Path)
    ap.add_argument("--fixtures", type=Path, help="dir of <slug>.json files (offline test)")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="write only the digest; don't send or update inputs/state")
    args = ap.parse_args(argv)
    args.no_email = args.no_email or args.dry_run

    cfg = load_config(args.config)
    now = datetime.now(timezone.utc)
    slugs = load_slugs(args.companies)
    reg = load_registry(args.registry)
    seen = load_json(args.seen, {})
    if not args.fixtures:
        try:
            notion = load_slugs_notion(cfg)
        except Exception as e:
            print(f"[notion] could not read database: {e}", file=sys.stderr)
            notion = None
        if notion is not None:
            print(f"[notion] {len(notion)} companies from Notion", file=sys.stderr)
            known = {s.lower() for s in slugs}
            slugs += [s for s in notion if s.lower() not in known]

    own = {s.lower() for s in slugs}          # your list: always checked, failures reported
    new_slugs = set()
    if not args.fixtures:
        new_slugs = {s for s in discover_slugs(cfg) if s.lower() not in own}
        if new_slugs:
            if not args.dry_run:
                with open(args.companies, "a", encoding="utf-8") as f:
                    f.write(f"\n# discovered {now:%Y-%m-%d}\n" + "\n".join(sorted(new_slugs)) + "\n")
            slugs += sorted(new_slugs)
            own |= {s.lower() for s in new_slugs}

    harvested = 0
    if not args.fixtures:
        harvested = update_registry(reg, cfg, now)
        extra = [s for s in pick_registry_boards(reg, now, cfg) if s.lower() not in own]
        print(f"[registry] {sum(1 for k, v in reg.items() if k != '_meta' and v.get('de'))} "
              f"German companies known · +{harvested} new from crawl · "
              f"checking {len(slugs)} listed + {len(extra)} crawled", file=sys.stderr)
        slugs = slugs + extra

    boards, bad, failures = {}, [], []
    if args.fixtures:
        for p in sorted(args.fixtures.glob("*.json")):
            boards[p.stem] = json.loads(p.read_text())["jobs"]
    else:
        with ThreadPoolExecutor(max_workers=cfg.get("workers", 16)) as pool:
            for slug, jobs, err in pool.map(fetch_board, slugs):
                registry_slug = slug.lower()
                if jobs is None:
                    failures.append((slug, err))
                    if slug.lower() in own:
                        bad.append((slug, err))
                    if registry_slug in reg and err == "HTTP 404":
                        reg[registry_slug] = {**reg[registry_slug], "de": False}
                else:
                    boards[slug] = jobs
                    previous = reg.get(registry_slug, {})
                    since = publish_cutoff(seen, cfg, now, previous)
                    reg[registry_slug] = {**previous, "de": has_germany(jobs, cfg),
                                          "checked": now.date().isoformat(),
                                          "eligible_since": since.isoformat()}

    # Crawl URLs can outlive a board; count 404s separately from fetch errors.
    errors = [(slug, err) for slug, err in failures if err != "HTTP 404"]
    not_found = len(failures) - len(errors)
    matches = curate(boards, cfg, seen, now, registry=reg)
    picks = matches[: cfg["max_results"]]
    stats = {"boards_ok": len(boards), "bad_slugs": bad, "new_slugs": new_slugs,
             "boards_requested": len(boards) + len(failures), "boards_failed": len(errors),
             "boards_not_found": not_found, "dry_run": args.dry_run,
             "harvest_incomplete": bool(cfg.get("harvest", {}).get("enabled", True)
                                        and reg.get("_meta", {}).get("harvest_incomplete")),
             "jobs_total": sum(len(j) for j in boards.values()),
             "days": cfg["lookback_days"], "n_new": sum(1 for j in picks if j.get("_new")),
             "mode": cfg.get("digest_mode", "new_only")}

    args.out.mkdir(parents=True, exist_ok=True)
    md, page = render_md(picks, now, stats), render_html(picks, now, stats)
    (args.out / "digest.md").write_text(md, encoding="utf-8")
    (args.out / "digest.html").write_text(page, encoding="utf-8")
    (args.out / "digest.json").write_text(json.dumps(
        [{"title": j["title"], "company": j["_slug"], "location": j["_loc"],
          "published": j["_pub"].isoformat(), "url": j["jobUrl"], "work_style": j["_style"],
          "german_required": j["_german"], "compensation": j["_comp"]} for j in picks],
        indent=2, ensure_ascii=False), encoding="utf-8")

    report = {"generated_at": now.isoformat(), "boards_requested": stats["boards_requested"],
              "boards_ok": len(boards), "boards_failed": len(errors), "boards_not_found": not_found,
              "jobs_total": stats["jobs_total"], "matches": len(picks), "matches_total": len(matches),
              "harvest_incomplete": stats["harvest_incomplete"],
              "completed_crawls": reg.get("_meta", {}).get("completed_crawls", []),
              "failures": [{"company": slug, "error": err} for slug, err in errors]}
    write_json(args.out / "run.json", report)
    print(f"[fetch] {len(boards)}/{stats['boards_requested']} boards fetched · "
          f"{stats['jobs_total']} jobs · {len(errors)} errors · {not_found} not found", file=sys.stderr)

    print(md)
    status = 0 if boards else 1
    delivered = set()
    delivery = "disabled" if args.no_email else "not needed"
    if not boards:
        delivery = "not attempted"
    if boards and not args.no_email and (stats["n_new"] or cfg.get("email_when_empty")):
        subj = f"Ashby radar: {stats['n_new']} new roles ({now:%d %b})"
        try:
            if send_email(subj, page, md):
                delivery = "sent"
                for j in picks:
                    seen.setdefault(j.get("id") or j["jobUrl"], now.date().isoformat())
                cutoff = (now - timedelta(days=cfg.get("forget_after_days", 60))).date().isoformat()
                seen = {k: v for k, v in seen.items() if k == "_last_run" or v >= cutoff}
                seen["_last_run"] = now.isoformat()
                write_json(args.seen, seen)
                delivered = {j.get("id") or j["jobUrl"] for j in picks}
                print("Email sent.")
            else:
                delivery, status = "not configured", 1
                print("[email] SMTP is not configured; jobs remain unsent", file=sys.stderr)
        except Exception as e:
            delivery = "sent; state save failed" if delivery == "sent" else "failed"
            status = 1
            report["delivery_error"] = f"{type(e).__name__}: {e}"
            print(f"[email] {delivery}: {e}", file=sys.stderr)
    report["delivery"] = delivery
    pending = [j for j in matches if j["_new"] and (j.get("id") or j["jobUrl"]) not in delivered]
    pending_boards = {j["_slug"].lower() for j in pending}
    for slug in boards:
        if slug.lower() not in pending_boards and slug.lower() in reg:
            reg[slug.lower()].pop("eligible_since", None)
    if not args.dry_run and not args.fixtures:
        write_json(args.registry, reg, one_per_line=True)
    report["pending_matches"] = len(pending)
    write_json(args.out / "run.json", report)
    if not boards:
        print("[fetch] no board could be fetched; notification progress was preserved", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())
