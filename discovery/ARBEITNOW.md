# Arbeitnow investigation — 1 November 2026

Arbeitnow is the German service Stefano mentioned. Its [official API overview](https://www.arbeitnow.com/blog/job-board-api)
links a free, unauthenticated JSON feed at
`https://www.arbeitnow.com/api/job-board-api?page=1` and
[Postman documentation](https://documenter.getpostman.com/view/18545278/UVJbJdKh).
It aggregates multiple ATSs, including JOIN, Greenhouse and Recruitee.

Claude's measured walk: 20 pages at three-second intervals, 4,359 rows / 4,356
distinct full slugs at 1,675 companies. Follow `links.next` until null; page
sizes vary and the `meta.from` offset is unreliable. The response advertised a
50-request limit without specifying the window. Independently cached pages
had overlapping IDs, so this is not a consistent snapshot.

The observed feed covers roughly seven days, rather than every open job.
`created_at` reflects import time: among 52 company/title matches with our
catalog, the median lag behind the native ATS posting date was 1.6 days, and
24 native postings were over seven days old. Some imported ads had already
closed. It cannot supply reliable publication dates or closure decisions for
our catalog.

Fields include the full slug, company name, title, HTML description, location,
remote flag, tags/job types, import timestamp and an Arbeitnow URL. There is
no stable employer ID, company website, native ATS identifier or salary field.
The numeric slug suffix is not unique: retain the entire slug if experimenting.

Her current filters produced 182 matches at 128 companies. A company/title
comparison found 52 on our board and up to 130 beyond it; the latter is an
upper bound, not a verified count of additional open jobs. Fifteen sampled job
pages included ten JOIN listings. Workable is the simpler next source because
its existing adapter provides native IDs and company metadata. JOIN is a useful
separate lead for finding more German companies.

## Republication is unresolved

The [terms](https://www.arbeitnow.com/terms) have a general restriction on public
display/mirroring in section 2, but section 11 explicitly discusses API use and
requires a link back on the user's platform. The API notice also refers to
those terms. These provisions need clarification before treating the feed as
a public republication license. We have not added an Arbeitnow provider or
republished its descriptions. The owner advertises a tailored paid API in the
overview; contacting them is a separate decision, and no message was sent.

For discovery, the investigation revealed `greenhouse:usc` (Urban Sports Club),
which differs from the previously known `urbansportsclub`. A direct native
Greenhouse API check confirmed three open jobs, including two German jobs;
neither matches the current city/remote focus, so it adds zero current catalog
matches. It is recorded as a candidate rather than imported here. No scalable
walk of Arbeitnow application redirects was implemented: those redirects are
undocumented and count as application clicks.

Detailed local notes and probe artifacts are referenced in the shared WORK.md
under "Claude Code — 1 Nov 2026: Arbeitnow API investigation". This investigation
used no Serper credits or AWS queries.
