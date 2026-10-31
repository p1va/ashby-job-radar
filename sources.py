"""Public job sources: board names and small adapters to the radar's job fields."""
from __future__ import annotations

import html
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

SUPPORTED = ("ashby", "greenhouse", "workable")
GREENHOUSE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
RESERVED = {"", "api", "embed", "static", "favicon.ico", "robots.txt", "sitemap.xml"}


def provider(key: str) -> str:
    return key.split(":", 1)[0] if ":" in key else "ashby"


def enabled(cfg: dict) -> list[str]:
    values = cfg.get("sources", ["ashby"])
    if not isinstance(values, list) or not values or any(v not in SUPPORTED for v in values):
        raise ValueError("sources must be a nonempty list containing ashby, greenhouse or workable")
    return list(dict.fromkeys(values))


def board_key(value: str) -> str:
    """Bare names remain Ashby; full URLs must belong to a supported board host."""
    value = value.strip()
    source = "ashby"
    if any(value.lower().startswith(host + "/") for host in {"jobs.ashbyhq.com"} | GREENHOUSE_HOSTS):
        value = "https://" + value
    if "://" in value or value.startswith("//"):
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.port:
            raise ValueError("Expected an http(s) job board URL")
        host = (parsed.hostname or "").lower()
        parts = parsed.path.strip("/").split("/")
        if host == "jobs.ashbyhq.com":
            value = parts[0]
        elif host in GREENHOUSE_HOSTS:
            source = "greenhouse"
            value = (parse_qs(parsed.query).get("for") or [""])[0] if parts[0] == "embed" else parts[0]
        else:
            raise ValueError("Unsupported job board host: " + host)
    elif ":" in value:
        source, value = value.split(":", 1)
        source = source.lower()
        if source not in ("ashby", "greenhouse"):
            raise ValueError("Unsupported board provider; Workable uses the search, not pasted boards")
    value = unquote(value).strip().lower()
    if value in RESERVED or any(c in value for c in "/\\?#:{}") or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid company board name")
    if source == "greenhouse" and not re.fullmatch(r"[a-z0-9_-]+", value):
        raise ValueError("Invalid Greenhouse board token")
    return ("greenhouse:" if source == "greenhouse" else "") + value


def select(keys, cfg: dict) -> list[str]:
    active = enabled(cfg)
    # Workable is collected in one search, never through the per-board worker pool.
    return sorted({key for key in keys if provider(key) in active and provider(key) != "workable"})


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif not self.hidden and tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ("p", "div", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = PlainText()
    parser.feed(value)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(parser.parts)).strip()


def jobs_list(body: bytes) -> list[dict]:
    data = json.loads(body)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("response has no jobs list")
    if not all(isinstance(j, dict) for j in data["jobs"]):
        raise ValueError("response contains an invalid job")
    total = (data.get("meta") or {}).get("total")
    if total is not None and total != len(data["jobs"]):
        raise ValueError("response has an incomplete jobs list")
    return data["jobs"]


def greenhouse_job(job: dict) -> dict:
    if not job.get("id") or not isinstance(job.get("title"), str):
        raise ValueError("Greenhouse job has no ID or title")
    content = html.unescape(job.get("content") or "")
    return {"id": "greenhouse:" + str(job["id"]), "source": "greenhouse",
            "companyName": (job.get("company_name") or "").strip(), "title": job["title"],
            "location": (job.get("location") or {}).get("name") or "",
            "secondaryLocations": [], "workplaceType": "", "isListed": True,
            "publishedAt": job.get("first_published") if isinstance(job.get("first_published"), str) else None,
            "descriptionHtml": content, "descriptionPlain": plain_text(content),
            "jobUrl": job.get("absolute_url") or "",
            "department": " / ".join(d.get("name") or "" for d in job.get("departments") or [])}


def fetch_greenhouse(key: str, request, candidate=None) -> list[dict]:
    url = "https://boards-api.greenhouse.io/v1/boards/" + quote(key.split(":", 1)[1], safe="") + "/jobs"
    light = [greenhouse_job(j) for j in request(url, parse=jobs_list)]
    if not any(candidate(j) if candidate else True for j in light):
        return light
    # A second complete board snapshot handles postings closed between requests.
    # A transient error propagates: this board's eligibility checkpoint must stay put.
    detailed = request(url + "?content=true", parse=jobs_list)
    for job in detailed:
        if (candidate is None or candidate(greenhouse_job(job))) and not isinstance(job.get("content"), str):
            raise ValueError("Greenhouse detail response has no job content")
    return [greenhouse_job(j) for j in detailed]


def workable_job(job: dict) -> tuple[str, dict]:
    company = job.get("company") or {}
    if not job.get("id") or not company.get("id") or not isinstance(job.get("title"), str):
        raise ValueError("Workable job has no job ID, company ID or title")
    location = job.get("location") or {}
    content = "\n".join(job.get(k) or "" for k in ("description", "requirementsSection", "benefitsSection"))
    label = ", ".join(filter(None, [location.get("city"), location.get("subregion"), location.get("countryName")]))
    style = {"remote": "Remote", "hybrid": "Hybrid", "on_site": "OnSite"}.get(job.get("workplace"), "")
    return "workable:" + company["id"], {
        "id": "workable:" + str(job["id"]), "source": "workable", "title": job["title"],
        "companyName": company.get("title") or "", "companyWebsite": company.get("website") or "",
        "companyLogoUrl": company.get("image") or "", "companyIntroduction": plain_text(company.get("description") or ""),
        "location": label, "secondaryLocations": [{"location": v} for v in job.get("locations") or []
                                                    if isinstance(v, str) and v not in ("TELECOMMUTE", label)],
        "workplaceType": style, "isRemote": style == "Remote", "isListed": job.get("state") == "published",
        "publishedAt": job.get("created") if isinstance(job.get("created"), str) else None,
        "descriptionHtml": content, "descriptionPlain": plain_text(content),
        "jobUrl": job.get("url") or "", "employmentType": job.get("employmentType") or "",
        "department": job.get("department") or ""}


def fetch_workable(request, *, delay: float = 2, max_pages: int = 500) -> dict[str, list[dict]]:
    """Optional undocumented Germany search. A partial cursor walk never counts as success."""
    boards, ids, tokens = {}, set(), set()
    token, expected = None, None
    for page in range(max_pages):
        if page:
            time.sleep(delay)
        query = {"location": "Germany"}
        if token:
            query["pageToken"] = token
        data = request("https://jobs.workable.com/api/v1/jobs?" + urlencode(query), parse=json.loads)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError("Workable search response has no jobs list")
        total = data.get("totalSize")
        if total is not None:
            if type(total) is not int or total < 0:
                raise ValueError("Workable search has an invalid total")
            expected = total
        for job in data["jobs"]:
            if not isinstance(job, dict):
                raise ValueError("Workable search contains an invalid job")
            company, normalized = workable_job(job)
            if normalized["id"] not in ids:
                boards.setdefault(company, []).append(normalized)
                ids.add(normalized["id"])
        token = data.get("nextPageToken")
        if not token:
            if expected is not None and len(ids) < expected:
                raise ValueError("Workable search ended before all advertised jobs were returned")
            return boards
        if not isinstance(token, str) or token in tokens or not data["jobs"]:
            raise ValueError("Workable search has an empty page or repeated/invalid cursor")
        tokens.add(token)
    raise ValueError("Workable search exceeded the page limit; no partial result accepted")
