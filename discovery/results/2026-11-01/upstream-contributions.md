# Upstream contributions — 1 November 2026

Owner base: `d9ea8e7` (includes her newsletter addition). These are focused ports
onto her current code; no PR was merged and no email workflow was dispatched.

| PR | Change | Head | Dependency |
|---|---|---|---|
| [9](https://github.com/betklab/ashby-job-radar/pull/9) | Compact descriptions and list bullets | `4527f12` | Independent |
| [10](https://github.com/betklab/ashby-job-radar/pull/10) | Greenhouse, profiles and 4,639 verified boards | `5ff7d0b` | Independent |
| [11](https://github.com/betklab/ashby-job-radar/pull/11) | Optional remote scopes and former-role title matching | `076a41f` | Independent; defaults false |
| [12](https://github.com/betklab/ashby-job-radar/pull/12) | Workable discovery and 273 company identities | `ff77d71` | Draft after 10; catalog opt-in |
| [13](https://github.com/betklab/ashby-job-radar/pull/13) | Parallel collection, bounded fallback and catch-up preview | `356f5a6` | Draft after 10 and 12 |

All five branches merge together without conflicts. The combined tree passes
61 offline tests. A complete mixed replay renders 1,058 roles at 357 companies
(540 Ashby, 333 Greenhouse, 185 Workable), with no duplicate IDs or excess
paragraph/bullet gaps; the existing newsletter signup and repository footer stay.
All 3,660 original registry values and crawl metadata are preserved. Company
choices, embedded list, sent history and daily/discovery workflows are unchanged.

The native Workable port completed 145 successful requests in 344.2 seconds:
2,893 jobs across 273 companies, yielding 185 matching jobs at 58 companies under
the owner's settings. The included company inventory is a dated reference;
normal builds always fetch current jobs. No company-seeding or database setup
is needed. Workable remains an undocumented, optional catalog source.

[Pipeline failure/retry test](https://github.com/p1va/ashby-job-radar/actions/runs/41324731556):
- Attempt 1 deliberately failed Greenhouse after writing diagnostics. Ashby and
  combine succeeded: 541 fresh roles, Greenhouse unavailable, 410 unseen preview
  jobs. Deployment was skipped.
- Re-running failed jobs replaced the Greenhouse artifact without a name conflict.
  Combine accepted the original Ashby collection plus the retried Greenhouse
  collection: 874 roles (541 + 333), both fresh, no duplicate IDs, partial false,
  743 unseen preview jobs. The company cache was reused. Deployment stayed skipped.
- The fault hook exists only on the test branch, never on a PR branch. The flattened
  single-artifact case has its own regression test and was previously proven live
  on the fork's original pipeline.

Fork data alignment `e911c56` copies the owner's latest company/embedded lists,
registry values, sent state and digests, retaining all 4,639 Greenhouse entries,
378 company profiles, the featured list and four fork preferences. The registry
now has 8,299 company keys (3,660 Ashby + 4,639 Greenhouse).
[Pages run](https://github.com/p1va/ashby-job-radar/actions/runs/41323714853) succeeded;
the live board was verified at 1,235 roles / 432 companies, all three sources fresh.

Claude Code independently reviewed the ports, combined merge and live runs.
