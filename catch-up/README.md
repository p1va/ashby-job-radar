# Catch-up preview

An on-demand report of **every currently listed matching job absent from the
saved sent history**. It uses the radar's existing title, location, focus-city,
work-style, compensation and language helpers. It ignores publication cutoffs,
the weekly board rotation and `max_results`.

This POC produces a preview. It never sends email or updates `seen.json`,
`_last_run`, `boards.json` or the company lists. Opening a report does not mark
its roles as sent. There are no search API calls or Serper credits involved.

## Run from Actions on our fork

Choose **Actions → Catch-up preview → Run workflow**.

The workflow fetches the original repo's latest `config.json`, `seen.json` and
company list, and combines those companies with our fork's saved registry and
lists. If the original repo also tracks a registry or embedded-board list, those
are included too. While `pr/5-serper` exists in our fork, it also includes that branch's
company and embedded-board lists. The run records the exact source commits and
input hashes. It reads public repository data and needs no SMTP or search keys.

The run summary shows the first 20 matches. Download its artifact and open
`digest.html` for the **complete** report; CSV, JSON and Markdown are included.
The workflow has read-only repository permissions and no state-saving step.

## Run locally

From the fork checkout:

```sh
python3 -B catch-up/preview.py
```

The default uses this checkout's config, sent history, `boards.json`,
`companies.txt` and `embedded-boards/boards.txt` if present. Check that the history
belongs to the person whose unseen jobs you want: our fork's no-email test runs
do not advance her original repo's history.

Use explicit snapshots when comparing against the original repo:

```sh
python3 -B catch-up/preview.py \
  --config /path/to/original/config.json \
  --seen /path/to/original/seen.json \
  --registry boards.json \
  --companies companies.txt \
  --companies /path/to/original/companies.txt
```

Repeat `--companies` to include additional lists. All source slugs are combined
case-insensitively, including companies previously classified as non-German.
There is no new Common Crawl harvest or Google discovery during catch-up.
Notion-only companies are covered if they are already in one of the supplied
lists or registry; this preview does not query Notion.

For a small live sample, repeat `--board`:

```sh
python3 -B catch-up/preview.py --board clera --board n8n --board kombo
```

Use `--cache` to save fetched board responses under the output directory. Then
repeat the filtering against those saved responses without network calls:

```sh
python3 -B catch-up/preview.py --cache --board clera --board n8n
python3 -B catch-up/preview.py --fixtures data/exploration/catch-up-PREVIOUS_RUN/boards
```

Fixture runs are labeled as replays, not current live checks. `--out` chooses a
new output directory; the default is `data/exploration/catch-up-<UTC time>`.
`--workers` controls live concurrency (default 8, maximum 32).

## What counts as unseen

- A role must be currently listed and pass the same title and location/focus
  predicates as the installed radar.
- A matching ID **or job URL** already in `seen.json` excludes the role.
- Repeated copies of the same job across boards are included once.
- Old publication dates do not exclude a job. Missing dates are included and
  displayed as "Date not provided".
- All unseen matches appear in the full reports, including roles beyond 100.

**The history has a retention limit.** The radar normally forgets sent IDs after
60 days (`forget_after_days`). An older job missing from that retained file may
have been emailed before. This preview means "absent from the supplied saved
history", not "provably never received". It does not resurrect closed jobs.

The POC deliberately keeps catch-up separate from daily progress. A future send
mode should record the specific delivered job IDs only after successful delivery
and preserve the normal daily cursor. That delivery behavior is not implemented.

## Outputs

| File | Contents |
| --- | --- |
| `digest.html` | Complete report, newest first, undated jobs last |
| `digest.md` | Complete Markdown report |
| `matches.csv`, `matches.json` | Every unseen match with metadata and links |
| `summary.md` | Counts and the first 20 matches for the Actions summary |
| `boards.csv` | Every checked board, job count and fetch status |
| `run.json` | Scope, counts, skipped seen matches, input hashes and failures |

A missing or invalid sent-history file stops the run instead of assuming
everything is unseen. Missing boards (404) are reported separately from temporary
failures. Partial results are kept; unavailable boards make the run fail visibly.
An empty board is a successful fetch. If no board can be fetched, the report says
so and the run fails.

## Offline checks

```sh
python3 -B -m unittest discover -s catch-up -q
```

Checks cover seen-ID and URL exclusion, age and result-limit bypass, unchanged
inputs, ordinary filter parity, deduplication, missing dates, complete exports,
cache replay, missing/corrupt history and partial fetch failures.

## First live preview — 26 October 2026

[Actions run 40318801993](https://github.com/p1va/ashby-job-radar/actions/runs/40318801993)
succeeded using the original repo's `9e57b2e` config and history snapshot (71
saved job IDs, last normal run 26 Oct at 11:29 UTC). It also included the fork's
registry and `pr/5-serper` company sources at `f7683c9`.

| Measure | Result |
| --- | --- |
| Boards selected from all sources | 2,213 |
| Boards fetched | 2,007 |
| Boards no longer found | 206 |
| Other fetch errors | 0 |
| Listed/API jobs read before filtering | 45,665 |
| Matching jobs before seen exclusion | 469 |
| Matching jobs already in saved history | 64 |
| **Unseen matches in the complete report** | **405, across 141 companies** |

Verified all 405 exported IDs and URLs are absent from the supplied history;
HTML, Markdown, CSV and JSON contain all 405, while the run summary shows 20.
The oldest listed unseen match was published in December 2024, demonstrating
that the daily cutoff was bypassed. Historical retention still applies to what
can be known about previous delivery. No notification or registry state was
advanced, and no email was sent. The artifact is retained for 30 days.
