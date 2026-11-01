# Company information and featured companies

`companies.json` is a persistent lookup keyed by board: a bare Ashby slug or `greenhouse:<token>`. It stores the real name, website, careers URL, logo, board introduction, source and fetch date where the provider publishes them (Ashby also gives the organization ID and timezone). The site uses the real name, logo, website and plain-text introduction when available. Missing or failed enrichment falls back to the board slug without removing any jobs. Workable is skipped: its job responses already carry company details.

Greenhouse has no logo field in its public API. For each Greenhouse company the refresher makes two requests:
- the public board API `boards-api.greenhouse.io/v1/boards/<token>`, for the name and board introduction;
- the hosted board page `job-boards.greenhouse.io/<token>` (that host also serves EU boards; `job-boards.eu.greenhouse.io` is tried only after a 404). The page's `og:image` is kept only when it is a logo uploaded to Greenhouse (`*recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/logos/…`).

Redirects are not followed: a redirect means the company runs its own careers site, so it gets no logo rather than that site's banner. In a live run on 1 November 2026, 63 of the 115 Greenhouse companies on the board got a logo and 54 an introduction, with no failures.

## When to fetch

**Refresh company profiles** runs weekly on Monday at 05:17 UTC, and on demand in the fork's Actions tab. It reads the companies represented on the published board, rather than fetching profiles for every discovered board. Profiles younger than 30 days are reused. Newly represented companies and older profiles are fetched with paced public requests: one GraphQL request per Ashby company, and two (occasionally three) per Greenhouse company. Select **force** to ignore the cache lifetime, optionally giving a single **board** slug for a targeted refresh.

The daily job-board build only reads the cache. It never waits on enrichment or queries a profile per role. A profile refresh checkpoints each successful result, commits successful progress even if another company fails, and explicitly starts **Publish job board** if the cache changed. Failed enrichment stays visible in the Action result and report. Last good profiles and companies no longer represented are preserved. A rate limit or server outage stops the rest of that provider's batch after the existing bounded retries; the other provider carries on.

No API keys are required. This uses the public hosted board's GraphQL endpoint, not an employer's authenticated API. Its schema can change, so GraphQL errors are checked even for HTTP 200 responses. Company timezone is retained as metadata; it is not treated as headquarters. No headquarters or industry is inferred. A board introduction is company-supplied board text, not a verified company overview.

```sh
python -B company-info/refresh.py --report /tmp/company-report.json
# A small subset, or replay a local published companies.json as the input list:
python -B company-info/refresh.py --catalog /tmp/companies.json --board n8n --report /tmp/company-report.json
```

The published `/companies.json` is the current company lookup, including role counts and featured flags. The repository cache retains the underlying GraphQL metadata separately. Build artifacts contain rendered text, never employer-supplied HTML.

## Choose featured companies

Edit [`featured.json`](featured.json): a simple JSON array of board slugs, independent of fetched metadata.

```json
["n8n", "langfuse", "openai"]
```

The initial list is the **42 companies in the original `companies.txt` at commit `6d3cbc4`**, chosen by Stefano. It deliberately excludes companies added by automated discovery. A featured company gets a star badge when it has current matching roles; the site also has a featured-only filter and a “Featured first” sort. This is a shared curated list, not browser-local favorites or sponsored placement. Editing it triggers site publication. No current role filters are bypassed.

```sh
python -B -m unittest discover -s company-info -p 'test_*.py'
```
