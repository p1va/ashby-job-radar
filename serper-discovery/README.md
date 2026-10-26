# Discover Ashby companies with Serper

Run Google queries through [Serper](https://serper.dev/), extract Ashby board
slugs, and check each unique board through Ashby's public posting API. This is
a standalone discovery tool: it can add companies to `boards.json` for the
existing radar to check on its next run.

Python 3.9+, standard library only. Set `SERPER_API_KEY` in your environment;
the script does not load `.env` files. It does not send email or update
`seen.json`, `companies.txt`, the radar config, or the workflow.

## Run it

From the repository root:

```sh
python3 serper-discovery/discover_boards.py
```

The default is **report only**, using these queries:

```text
site:jobs.ashbyhq.com ("product manager" OR "GTM" OR "founder associate" OR "operations" OR "automation" OR "marketing") "Berlin"
site:jobs.ashbyhq.com "Berlin"
ashby Berlin
```

Each query fetches up to two pages of ten results, for at most six Serper
requests. Results are requested with `gl=de` and `hl=en`. Change these with
`--country` and `--language`. Stop conditions include empty pages and pages
containing only URLs already returned for that query. Finding only companies
already in the registry does not stop pagination.

The default registry is `boards.json` beside this folder. If it is absent, the
comparison starts with an empty list, so all live discoveries count as new.
Pass `--boards /path/to/fork/boards.json` to compare against a saved registry.

To merge verified live companies into that registry:

```sh
python3 serper-discovery/discover_boards.py --add
```

New companies get `{"de": null}` so the existing radar checks and classifies
them. Existing company state and `_meta` are preserved. The registry is re-read
before merging and written atomically in the radar's one-company-per-line
layout. This is not a lock between simultaneous writers: run additions when
another process is not updating the same registry.

## Try narrower or broader searches

Repeat `--query` to replace the defaults with your own queries:

```sh
python3 serper-discovery/discover_boards.py \
  --query 'site:jobs.ashbyhq.com "product manager" "Berlin"' \
  --query 'site:jobs.ashbyhq.com "marketing" "Berlin"' \
  --query 'site:jobs.ashbyhq.com "operations" "Berlin"' \
  --pages 3
```

Splitting roles into separate searches may surface companies hidden below the
first pages of a combined query. More queries and pages mean more Serper calls.
`--pages` accepts 1–10.

For a recency comparison:

```sh
python3 serper-discovery/discover_boards.py \
  --query 'site:jobs.ashbyhq.com "Berlin"' --recent month
```

`--recent day|week|month|year` sends Google's corresponding `tbs=qdr:...`
parameter through Serper. The default has no date restriction to retain older
company discoveries.

**A date filter is not an “open jobs only” filter.** The script verifies boards
against the [posting API](https://developers.ashbyhq.com/docs/public-job-posting-api)
and compares indexed job IDs with their currently listed jobs. A closed indexed
job can still lead to a valid company board with other openings, so its company
remains eligible for discovery. An empty, valid board is also retained.

Google's [`site:` results are not exhaustive](https://developers.google.com/search/docs/monitor-debug/search-operators/all-search-site).
Combine this source with Common Crawl and embedded-board discovery; do not
interpret the number of search results as the total number of Ashby companies.

## Follow company-hosted results

The broad `ashby Berlin` query can return company careers pages rather than
hosted Ashby URLs. To inspect a limited number of those pages for Ashby links:

```sh
python3 serper-discovery/discover_boards.py \
  --query 'ashby Berlin' --follow-sites 10
```

The number limits unique external result URLs fetched, including unsuccessful
attempts. This adds ordinary website requests. It reads HTML, including common
JSON-escaped links; it does not run a browser or guess slugs from domain names.
Discovered links still require a valid Ashby board response. Finding an Ashby
link on a page does not establish that site's ownership of the board; the
report keeps the source URL without importing company website metadata.

## Reports and replay

Each run creates a new directory under `data/exploration/serper-<UTC time>`.
Use `--out /path/to/new-directory` to choose another location.

| File | Contents |
| --- | --- |
| `run.json` | Request/credit counts, discoveries, additions and errors |
| `boards.csv` | One row per candidate, live status, known/new, listed-job and Berlin-job counts |
| `search-results.csv` | Query, page, result URL, board slug and current/stale indexed-job check |
| `new-slugs.txt` | Verified live slugs absent from the initial registry |
| `live-slugs.txt` | All verified live slugs from this run |
| `searches/*.json` | Search request parameters and responses, without authentication headers |
| `boards/*.json` | Full public API responses for valid boards |
| `pages/*.html` | External pages inspected with `--follow-sites` |

`berlin_jobs` counts listed jobs whose primary or secondary location/address
mentions Berlin. It does not infer a company's headquarters or apply the
digest's role preferences. Raw board responses allow those filters to be
applied later.

To repeat validation or add companies from saved search results without
another Serper search:

```sh
python3 serper-discovery/discover_boards.py \
  --replay data/exploration/serper-PREVIOUS_RUN --add
```

Replay needs no Serper key, but still checks boards through Ashby's API and
visits external pages if requested. It produces a new report directory.

Searches run serially and are not automatically retried: a timed-out paid
request may already have consumed a credit. A search error stops further paid
requests and preserves completed pages for validation and replay. Board GETs
have bounded retries for transient errors, including 429/5xx, honoring a short
`Retry-After`; 404 is reported separately. Individual board/page failures do
not discard other results. Errors produce a nonzero exit code and a report.
`credits_reported` totals credits returned in successful Serper responses;
the provider dashboard is authoritative for billing.

The API key stays in the environment and request headers. It is not written to
the report or included in console output.

## Checks

```sh
python3 -m unittest discover -s serper-discovery -q
```

Offline regressions cover pagination after known results, deduplication, active
and stale jobs, empty/missing/error boards, date filters, response gzip/retries,
partial paid-search failure, replay, bounded external-page discovery and atomic
registry merges preserving existing state.

## Trial on 26 October 2026

Compared against the fork's 2,144-company registry at `923311b`:

| Search | Requests | Live boards | Absent from the starting registry |
| --- | --- | --- | --- |
| Three defaults, two pages each | 6 | 27 | 10 |
| Broad Ashby-site query, past month, one page | 1 | 8 | 3 |
| Six individual role queries, one page each | 6 | 35 | 8 |
| Union of these runs | 13 | 49 | 15 |

Rows overlap. The exact combined query found 13 boards, including five absent
from the registry; the broader Ashby-site query found 17, including six absent.
Plain `ashby Berlin` added no boards, including after inspecting four external
results. Indeed blocked one optional page request with 403, which was recorded
without discarding the discoveries. One indexed board, `whatnot`, returned 404
from the posting API and was excluded from additions.

All 15 new-to-registry boards had currently listed jobs mentioning Berlin.
`augustus` was already in the working checkout's `companies.txt`; the other 14
were also absent from that list. These are coverage checks at trial time, not
guarantees that an individual role matches the digest or remains open later.

The 13 scripted searches reported 13 credits; an initial one-request API probe
used one additional credit. The past-month response echoed `tbs=qdr:m`.
The six individual searches included the literal `"founder associate"` phrase,
which returned no results in this sample; alternative phrasing can be tried
with `--query`.

Saved [board report](results/2026-10-26/boards.csv),
[result provenance](results/2026-10-26/search-results.csv) and
[run summary](results/2026-10-26/summary.json). Raw responses remain in the local
exploration directories. The script and fifteen offline regressions passed
Python 3.9 grammar checks; live trials used the configured Serper key.
