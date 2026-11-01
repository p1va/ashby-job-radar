# Description quality, remote coverage and Greenhouse logos

Stefano approved these three improvements for **p1va's fork** on 1 November.
The original repository, registry, delivery history, seeds and featured list
are unchanged. The fork explicitly opts into Europe/worldwide candidates and
matching primary job titles without parenthetical former-role background.

**Published and verified:** [run 41301590003](https://github.com/p1va/ashby-job-radar/actions/runs/41301590003)
passed all six jobs from `c8b999a`. The live board has **1,233 postings / 431
company keys**, 647 Ashby +402 Greenhouse +184 Workable, all fresh, no partial
refresh. There are **141 Remote Europe and 35 Remote Worldwide** candidates,
**3 ElevenLabs / 7 Supabase / 1 PostHog** matches and **78 logos among 139
represented Greenhouse companies**. Published descriptions have zero repeated
spacer-line runs or loose adjacent bullet pairs; IDs are unique. All 96 tests
pass, and local/live Chrome checks pass at 320–1440px. Claude independently
confirmed the final live payload. See [verification.json](verification.json),
[pages-run.json](pages-run.json) and [browser-live.json](browser-live.json).

## Changes

- One shared plain-text parser/normalizer removes spacer lines (including
  non-breaking spaces), preserves paragraphs and restores HTML list bullets.
  Indented HTML lists stay compact. The duplicated builder parser is removed.
- Europe/worldwide remote candidates require remote evidence. Their original
  location/country fields remain visible, with a note to check the source's
  permitted countries and timezones. They are not labelled Germany-confirmed.
- Europe takes priority over worldwide. Worldwide must be a full primary scope
  label (or structured worldwide country on a bare Remote posting), not a loose
  Global secondary tag, Global City place name, APAC-qualified label, or
  country-scoped Anywhere phrase. Bare Remote remains insufficient.
- An opt-in title rule ignores parenthetical background beginning ex-/former
  before both inclusion and exclusion. Product Engineer and internships stay
  excluded. Original titles are preserved.
- Snapshot inputs record the fork's three preference overrides. Collection
  reports now count exclusions by listing/title/location/focus reason.
- Greenhouse profiles use the public board API for name/introduction and the
  hosted board page for a strict Greenhouse-CDN logo path. Redirects aren't
  followed, so company-site banners aren't mistaken for logos. Page requests
  reuse gzip and bounded retries. Profile failures defer only their provider.

## Local verification before publication

The retained saved-response replay goes from **1,057 roles / 357 company keys**
to **1,233 / 431**, with **141 Remote Europe and 35 Remote Worldwide** postings.
It recovers **3 ElevenLabs, 7 Supabase and 1 PostHog** roles from the three
native API responses checked during the audit. Live job counts can change.

Repeated blank/spacer runs fall from **728 to zero**. Claude re-converted 2,306
cached Greenhouse descriptions containing list items and confirmed compact
bullets after the indented-list fix. **96 tests pass**, including real
US/APAC/Global City false-positive shapes, disabled flags, focus filtering,
parenthetical exclusions, profile cache/provider failures, no redirects and
gzip retries. Chrome checks pass search, filters, recovered job details,
source country evidence, Greenhouse image loading and 320–1440px layouts.

Claude's first profile probe supplied 115 Greenhouse profiles: 63 logos and 54
introductions, with no failures. Those results were reused. A paced follow-up
refreshed 114 additional profiles and reused 261 cached records. PostHog exposes
jobs but returned no public organization profile; its matching job remains in
the catalog with the normal identity fallback. The combined cache contains 80
Greenhouse logos; 78 are represented in this corrected preview. All 149 existing
profiles are semantically unchanged; 229 profiles were added.

Claude reviewed the logo patch and the matching/formatting changes over three
rounds, then approved the shared retry-opener integration. No emails, AWS
queries, Serper credits or upstream changes were involved.

See [local-verification.json](local-verification.json),
[company-refresh.json](company-refresh.json),
[greenhouse-profile-probe.json](greenhouse-profile-probe.json) and
[browser-local.json](browser-local.json). Full local native examples, screenshots
and the combined artifact are in `data/exploration/catalog-quality-20261101/`
in the shared workspace.
