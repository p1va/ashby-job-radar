# Athena Greenhouse backfill — 1 November 2026

Imported into **p1va/ashby-job-radar only**. No original-repository changes,
email, sent-history changes, AWS requests or Serper credits. Stefano exported
the Athena results himself. The application code and country scheduling policy
are unchanged.

Three CSVs contain **93,229 URLs → 4,811 supported board keys**. Of these, 351
were already registered and 4,460 needed checking. With four workers and
`--max-checks 5000`, validation found **4,283 live boards and 177 genuine 404s**.
One DNS failure succeeded on a separate retry; none remain unresolved or
deferred. Initial validation took 611.6 seconds. Retry HTTP statuses were not
instrumented during validation; the later full build was instrumented.

After peer review, **4,278 boards were added**: 264 with German jobs and 4,014
without. Five new mirror/repost boards were omitted, removing 21 matching
copies. Every pre-existing registry entry, including `_meta`, is unchanged.
Greenhouse now has **4,639 registered boards, 347 marked `de:true`**.

The retained snapshot has **873 matching postings at 299 company board keys**:
540 Ashby + 333 Greenhouse. The new boards contribute **114 postings at 59
keys**, after the 21 reviewed copies were removed. Counts are board keys,
not a company-identity census; the pre-existing Quality Group and Parloa pairs
remain. No general fuzzy deduplication was introduced. Two old Clera postings
were replaced by two different Clera postings between the baseline and current
fetch, leaving the existing-board total unchanged.

## Evidence

- [inputs.json](inputs.json): original filenames, hashes, row/host counts.
  CSV columns do not identify crawl partitions; no filename-to-index mapping
  is assumed. The original 7.6 MB of CSVs remain in the local exploration folder.
- [validation.csv](validation.csv): all 4,460 checks and each import decision.
- [import.json](import.json): exact added/excluded keys, scheduling estimates,
  recovered error and failure allowance.
- [new-matches.csv](new-matches.csv): 114 added matches, grouped by board.
- [mirror-review.json](mirror-review.json): the five omitted boards and 21
  title/location matches, retained IDs and publication deltas. Descriptions
  were also inspected. This is curation for today's matching catalog, **not**
  a claim that whole boards are equivalent or a permanent exclusion rule.
- [unsupported-tokens.json](unsupported-tokens.json): four valid punctuation
  tokens outside the current parser. Pyramid Healthcare has 357 jobs, none
  German; the other three boards are empty. These explain 52 rejected URL rows.
  Parser support is a separate change.
- [preserved-inputs.json](preserved-inputs.json): unchanged input/state hashes.
- [catalog-benchmark.json](catalog-benchmark.json): measured live build before
  review exclusions. The full normalized cache stays local.
- [final-replay.json](final-replay.json): retained catalog rendered offline
  from that cache, with 873 roles at 299 keys. Its 7,822 boards are the fetched
  boards only; the 493 real 404s are recorded in the live benchmark.

The omitted Doctolib and HelloFresh alumni boards have four German jobs absent
from the retained boards, but **none match today's role filters**. Their titles
are recorded in the review evidence. Revisit these exclusions if preferences
change. A future import can rediscover them: this change adds no permanent
skip list. Some provider company names include board labels, such as
"Wolt - English"; those names are preserved.

## Request cost and publication

The full measured live build before review fetched 7,827 of 8,320 requested
boards, with 493 real Ashby 404s, zero terminal errors and **zero Greenhouse
429s, including retries**. Greenhouse made 4,801 successful calls (4,644 lists
and 157 detail calls); one network error was retried successfully. Local wall
time was **790.295 seconds (13m10s)** and peak RSS **2,559 MiB**, including
writing the 1.2 GB optional normalized cache. GitHub Actions does not write that
cache. This measurement is one run, not a future rate-limit guarantee.

For the final retained import, the measured request subset is **4,278 extra
lists + 84 extra full-board detail calls per catalog build**, or 4,362 extra
successful requests at this snapshot (one additional retry occurred locally).
The fork schedules the catalog daily, so that is the current daily catalog cost.
The final total is 8,315 requested boards, including 4,639 Greenhouse boards.

If the email radar were run daily, its existing German-daily/other-weekly
schedule adds an average **837.4 list calls per day** (264 + 4,014/7), plus
details when title/location candidates exist. At this snapshot all 84 extra
detail calls are on German boards: roughly **921.4 extra requests per day**.
The email workflow was **not** dispatched to measure this.

Keeping only `de:true` would avoid 4,014 list calls per catalog build, but would
stop checking those boards for newly advertised German jobs. The all-country
policy is retained. Germany classification uses every lightweight job, not
only matching titles. There are 406 newly validated empty boards; those are
valid under the existing policy.

The existing per-provider publication allowance still applies: at 4,639
Greenhouse boards, **231 failed boards are tolerated; 232 block publication**.
Actual terminal failures were zero. An incomplete global provider scan always
blocks publication. Independent source pipeline steps and a wider deduplication
policy are deferred follow-ups, as requested.
