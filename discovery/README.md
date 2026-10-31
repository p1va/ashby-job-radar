# Additional job sources in the fork

The existing radar, catch-up preview and catalog share the same small provider
adapters in `sources.py`. Standard library only; keep running the existing
commands. Nothing in these discovery commands sends email or updates sent history.

```json
"sources": ["ashby", "greenhouse"]
```

Missing `sources` means Ashby only. The fork explicitly enables Greenhouse.
Workable is implemented but optional: a live local scan hit HTTP 429 on
31 October. It stays out of normal publication until its reliability is established.
Use **Actions → Preview additional sources → workable** to collect a separate
HTML/CSV/JSON artifact, without publishing or recording delivery. The scan is
serial, paced two seconds between pages, and discards incomplete results.

Bare company names are still Ashby. Add `greenhouse:n26` or paste a supported
Greenhouse URL into a company list. Search, filters and descriptions work across
providers; the catalog's **Listed on** filter can be linked with
`?source=greenhouse`. Workable uses company UUIDs and native job IDs; pasted
Workable company boards and fuzzy deduplication are intentionally unsupported.
Greenhouse office assignments are not treated as advertised work locations.
Publication dates stay unknown when the provider doesn't supply one. The board
keeps undated jobs, while the existing email date rule excludes them.

The fork's catalog continues taking role/location preferences from upstream,
but overlays the fork's own `sources` list. The original repository is unchanged.

## Serper backfill

Set `SERPER_API_KEY` in your environment, then:

```sh
python3 -B serper-discovery/discover_boards.py \
  --source greenhouse --pages 2 --add --out /tmp/greenhouse-search
```

Defaults cover Berlin, Munich, remote Germany and the EU board host. `--query`
can be repeated to override them. Each search page spends one credit; paid
requests are not automatically retried. All candidates are checked against the
live provider before adding them. Saved search responses can be reused with
`--replay /tmp/greenhouse-search` without spending more Serper credits.

## Common Crawl and Athena

```sh
python3 -B discovery/backfill.py --source greenhouse \
  --common-crawl --pattern 'job-boards.eu.greenhouse.io/*' \
  --add --out /tmp/greenhouse-crawl
```

Omit `--pattern` to search all three Greenhouse hosts. Requests are serial and
paced; malformed or failed pages are reported as incomplete. Completion is
recorded by crawl **and query pattern** in the run report, independently of the
existing Ashby crawl progress. Candidates and raw pages are retained locally.
Use `--replay /tmp/greenhouse-crawl` for subsequent validation without repeating
the crawl requests. `--max-checks` bounds validation; deferred candidates stay
in `candidates.json` and can be checked on another replay. Errors remain errors,
not empty or deleted boards.

For Athena, run [athena-greenhouse.sql](athena-greenhouse.sql) in your configured
account, adjusting the table and crawl partition. Export a CSV containing `url`:

```sh
python3 -B discovery/backfill.py --source greenhouse \
  --athena results.csv --add --out /tmp/greenhouse-athena
```

The same command accepts multiple `--athena` CSVs, or `--seeds` with a list of
board tokens, provider names or URLs. Only verified live boards are added;
existing entries and delivery progress are retained. Classification uses every
lightweight job, independent of title preferences.

The SQL finds hosted boards and Greenhouse-hosted embeds. A `gh_jid` on an
external careers site identifies a job, not its board token; those sites still
need their embed configuration inspected. The importer doesn't guess tokens
from company domains. Athena was not executed from this shell: no AWS CLI or
profile was available. SQL/import support is ready for an exported result.

## 31 October experiment

Compact reports are in [results/2026-10-31](results/2026-10-31). Six manually
checked seeds plus two Serper batches used 16 credits and established 43
Greenhouse boards in total. The live CDX attempt returned HTTP 502 after bounded
retries. Replaying Claude's same-day, saved EU index snapshot (373 candidates)
then checked 366 unknown tokens: 318 live, 77 with German jobs, 48 missing and
zero transient validation errors. These were added to the fork's registry.

The cached input was `cc-eu-slugs.json` from the investigation, SHA-256
`ad4b048da98c57881e2f97c283d66990f022004c362bee8e4664231443dfbb3b`.
The raw JSON arrays can be replayed through a `candidates.json` file of canonical
keys. This is evidence of the captured index and current board checks, not a
claim that today's CDX request or an Athena query succeeded.

Workable counts are based on location metadata. Ads recruiting in Germany for
relocation abroad can appear, and several cities can have separate IDs for the
same ad. We retain each ID's date, location and link rather than asserting
canonical posting identities.

## Verify changes

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
python3 -B -m unittest discover -s catch-up -p 'test_*.py'
python3 -B -m unittest discover -s job-board -p 'test_*.py'
python3 -B -m unittest discover -s company-info -p 'test_*.py'
python3 -B -m unittest discover -s serper-discovery -p 'test_*.py'
```

`job-board/build.py --cache` saves normalized responses beneath the new output
directory. Pass that `boards` directory to `--fixtures` to replay matching and
rendering offline. Catalog builds and source previews never advance email
checkpoints; failed required fetches leave the previous published catalog up.
