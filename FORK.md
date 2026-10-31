# Testing fixes in this fork

The fork also experiments with opt-in Greenhouse and Workable adapters. Normal
publication currently enables Ashby and Greenhouse; Workable has a separate
manual source preview while its public search's rate limits are evaluated.
See [additional sources and backfills](discovery/README.md). No changes or new
pull requests are made to the original repository by these experiments.

Greenhouse does not supply a structured remote/hybrid field. The 31 October
catalog has 182 of its 219 Greenhouse matches marked **Not stated** (33 Remote,
4 Hybrid); only explicit wording in the advertised location is used. This is
missing provider information, rather than an assumption that a role is office-based.

This fork is for trying small fixes to the existing script before offering
them to [the original project](https://github.com/betklab/ashby-job-radar).
Keep its standard-library implementation, settings, and filtering rules.

The fork's `main` contains the tested fixes together. Focused commits make it
easy to prepare upstream contributions later; separate fix branches are optional.

The fork's digest workflow is **manual only**. It passes `--no-email`, uses no SMTP
or Notion secrets, and saves runs on `main` back to this fork. It commits
`boards.json`, `companies.txt`, any delivery-state changes, and the current
files under `output/`. The original project's workflow is unchanged.

## Run a preview

Open [Actions → Digest preview (no email)](https://github.com/p1va/ashby-job-radar/actions/workflows/daily.yml)
and select **Run workflow**:

- Leave the workflow branch as `main`.
- Set `ref` to the branch, tag, or commit in this fork that you want to test.
- The default `full` run includes Common Crawl discovery. Use `listed` for a
  quick check of `companies.txt`. Full runs can take much longer.

The digest appears in the run summary. Download its Markdown, HTML, and JSON,
plus the `run.json` report, from the run's `digest-…` artifact, retained for 30 days.
Runs reuse the committed registry and delivery history. On `main`, the same
digest is copied to `output/` and committed with the updated registry, so the
run summary, artifact and repository show the same result. Runs queue and rebase
before pushing. A run against a different `ref` saves artifacts without pushing.

With `--no-email`, delivery history remains unchanged. Successful fetches update
company classifications and fetch dates; a separate eligibility checkpoint
keeps unsent roles available for later runs. The result limit remains unchanged.
Diagnostics and partial registry progress are saved after fetch failures too;
the run still reports failure when no board could be checked.

## Keep upstream pull requests focused

Develop and test fixes together on this fork's `main`. When preparing a PR,
start a contribution branch from the original project's latest `main` and bring
across the chosen fix commits. That keeps this document and the fork's no-email
workflow out of the upstream PR. The owner-facing workflow fixes remain on
`fix/reliability` and in the merged history; this fork keeps its preview workflow.

Work through the identified correctness issues in small patches: workflow/state
persistence together with cutoff recovery, incomplete Common Crawl harvests,
transient errors and gzip, preview side effects, delivery tracking, overflow,
and location/state handling. Preserve the owner's preferences and review changes
to notification behavior explicitly. The larger pipeline design remains parked.

## Catch-up preview

Use **Actions → Catch-up preview → Run workflow** to list all currently listed
matching jobs absent from the original repo's saved sent history. It bypasses
the daily date cutoff and result cap. Full HTML, CSV, JSON and Markdown reports
are uploaded as an artifact; the summary shows the first 20 matches.

This preview has read-only repository permissions and leaves delivery and board
state alone. See [the catch-up guide](catch-up/README.md), including the limits
of the retained sent history.

## Published catalog

[bet’s board](https://p1va.github.io/ashby-job-radar/) is a separate static catalog
of all current matches, including previously seen jobs. Search company, title,
locations and full descriptions; filter and sort in company or role views.
**Actions → Publish job board** rebuilds it on demand, on relevant code/source
changes, and daily at 06:35 UTC. No emails or delivery-state writes are involved.
See [the catalog guide](job-board/README.md) for local builds and publication.

**Refresh company profiles** enriches companies represented on the published
board, weekly or on demand, reusing profiles for 30 days. Names, websites and
logos are cached in `company-info/companies.json`. The separate
`company-info/featured.json` shared list starts with the original 42 company
seeds. See [company information](company-info/README.md) for editing and refreshes.
