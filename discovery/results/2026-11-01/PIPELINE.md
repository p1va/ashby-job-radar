# Parallel sources and Workable — 1 November 2026

Published on **p1va main only**, source commit `6be3417`, implementation
`b60259d`. [Production run 41295086405](https://github.com/p1va/ashby-job-radar/actions/runs/41295086405)
passed all six jobs. The three collectors started together at 09:54:12 UTC:

| Job | Duration | Matching postings |
| --- | ---: | ---: |
| Ashby | 65s | 540 |
| Greenhouse | 146s | 333 |
| Workable | 336s | 184 |
| Combine | 13s | 1,057 total |
| Deploy | 9s | — |

The whole run took 6m24s including preparation and job scheduling. Live
`jobs-83f25dab47c8.json` has **1,057 postings at 357 company board keys**, all
sources fresh, `partial:false`, a preferences hash and provider timestamps.
Claude independently checked the live payload and found zero duplicate IDs.
The combined catch-up artifact has **930 unseen matches**, excluding 127 seen
matches. It uses no daily cutoff or result cap and sends no email.

Workable's 184 postings represent **144 distinct company/title combinations**;
roughly 40 rows advertise another city for the same title. Recruiting agencies
and relocation ads are included when their advertised location meets the
existing filters. Native IDs and links are preserved; display grouping is a
follow-up. This expansion is experimental, not a claim of 184 unique new roles.

The separate local live probe used 145 requests, all HTTP 200, in 372.737s.
Actions reported zero terminal errors; retry HTTP statuses were not instrumented
there. Earlier Workable 429s remain a known reason for pacing and isolation.

[Fault run 41294777545](https://github.com/p1va/ashby-job-radar/actions/runs/41294777545)
deliberately failed Greenhouse and Workable on a separate test branch before
artifact creation. Only Ashby's artifact existed, and download-artifact placed
it directly in `collections/`. Recursive provider routing combined it
successfully: **540 postings / 184 companies, 413 unseen matches**, with both
failed sources explicitly unavailable. Deployment was skipped by the branch
guard; the overall red status was intentional. The test hook is absent from
production history.

A full-data offline failure replay kept all 184 previous Workable postings
with their original collection time, while the fresh-only digest dropped from
930 to 746 matches. Unit tests cover the 48-hour expiry, incompatible filters,
successful empty collections, missing/duplicate/malformed artifacts and all
sources failing. **78 tests passed in Actions.** Live browser checks cover
Workable filtering, native ID details, search, URLs and mobile widths
320/360/390/430/768/1024/1440px. A separate visual check injected stale and
unavailable notices and confirmed no horizontal overflow.

Evidence: [pipeline-pages-run.json](pipeline-pages-run.json),
[pipeline-verification.json](pipeline-verification.json),
[pipeline-failure-test.json](pipeline-failure-test.json),
[pipeline-fallback-local.json](pipeline-fallback-local.json),
[workable-probe.json](workable-probe.json). The registry, saved history,
company lists, profiles and featured list are unchanged. Only `sources` changed
in config. No AWS queries, Serper credits, email dispatches or upstream changes.

[Arbeitnow investigation](../../ARBEITNOW.md) remains research: useful discovery
leads, import timestamps rather than publication dates, and public reuse terms
that need clarification. No Arbeitnow job content was republished. An explicit
delivery step using the existing daily eligibility rules can be added later;
this fork currently saves the digest preview alongside the site.
