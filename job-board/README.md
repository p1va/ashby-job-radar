# bet’s board

A static job catalog for the fork, published at https://p1va.github.io/ashby-job-radar/.

It includes **every currently listed role matching the radar's location and title preferences**, including roles already emailed. It uses the same predicates as catch-up, with no sent-history check, publication cutoff or 100-role cap. Berlin, Munich and remote Germany are the current focus. This is a matching catalog, not every worldwide role at those companies.

The default view groups roles by company. Search covers company, title, locations, department and full descriptions; uncheck “Search job descriptions, too” to narrow it. Words are combined with AND; quotes keep phrases together. Location and work-style selections use OR within each filter, AND between filters. Filters and job details can be shared using the page URL. Sorting, a flat role view and a mobile filter panel are included.

Company names, websites, logos and board introductions come from a separately refreshed [company cache](../company-info/README.md). The shared featured list starts with the original 42 company seeds. Featured badges, a featured-only filter and “Featured first” sorting highlight those companies without changing ordinary role eligibility.

## Refresh

In the fork's **Actions → Publish job board → Run workflow**, or wait for the daily 06:35 UTC run. Changes to the catalog code and company sources also trigger a rebuild.

The workflow snapshots the original repository's current config and company sources at one commit. It combines them with the fork's registry, embedded boards and discovery seeds. Each known board is fetched once using the radar's existing public REST client, including compression and bounded retries. The board response already contains job descriptions, so there is no request per job.

No email is sent and no registry or sent history is changed. This workflow requires no search API key or SMTP secrets. A 404 removes a dead board from that build. Any other failed board stops publication, preserving the previous site. The Actions summary and a 30-day report artifact record counts, failures and source commits.

## Local use

Python 3.9+ and the standard library are sufficient:

```sh
python -B job-board/build.py --out /tmp/bets-board-build
python -m http.server 8000 --directory /tmp/bets-board-build/site
```

Open http://localhost:8000/. For a small live check, add `--board company-slug` (repeatable). To replay saved board responses, add `--fixtures path/to/boards`; each `<slug>.json` should contain `{"jobs": [...]}`. Output must be a new directory.

```sh
python -B -m unittest discover -s job-board -p 'test_*.py'
```

`build.py` selects records and writes HTML plus one JSON file. `index.html`, `styles.css` and `app.js` are the whole front end: no framework, package install or backend. Descriptions render as plain text, and employer HTML is never inserted into the page. Assets use relative, content-hashed paths so the site works under a GitHub Pages repository path without mixing cached versions. The collection is small enough to filter directly in the browser.

GitHub Pages must be enabled with **Source: GitHub Actions** in the fork's repository settings. `.github/workflows/job-board.yml` is restricted to this fork, so adding these files upstream does not automatically publish there.

Company display names are derived from Ashby board slugs, as in the digest. Location and work-style labels come from the job postings; they are not verified company headquarters. Older publication dates may still correspond to open listings. Follow the original posting for current availability and application details.
