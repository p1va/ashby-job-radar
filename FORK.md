# Testing fixes in this fork

This fork is for trying small fixes to the existing script before offering
them to [the original project](https://github.com/betklab/ashby-job-radar).
Keep its standard-library implementation, settings, and filtering rules.

The fork's workflow is **manual only**. It passes `--no-email`, uses no SMTP
or Notion secrets, has read-only repository permissions, and does not commit
state or results. The original project's email workflow remains unchanged.

## Run a preview

Open [Actions → Digest preview (no email)](https://github.com/p1va/ashby-job-radar/actions/workflows/daily.yml)
and select **Run workflow**:

- Leave the workflow branch as `main`.
- Set `ref` to the branch, tag, or commit in this fork that you want to test.
- Use `listed` for a quick check of `companies.txt`, or `full` to include
  Common Crawl discovery. Full runs can take much longer.

The digest appears in the run summary. Download its Markdown, HTML, and JSON
from the run's `digest-…` artifact, retained for 30 days. Every preview starts
with fresh temporary seen/registry state; it does not reuse the original
owner's email history or remember progress between runs. The existing first-run
publication cutoff and result limit still apply.

## Keep upstream pull requests focused

Start each fix branch from the original project's latest `main`, not this
fork's preview setup commit. Push the branch to this fork and test it by setting
the preview workflow's `ref` input. A PR from that fix branch will then contain
only the intended fix, without this document or the fork's workflow changes.

Work through the identified correctness issues in small patches: workflow/state
persistence together with cutoff recovery, incomplete Common Crawl harvests,
transient errors and gzip, preview side effects, delivery tracking, overflow,
and location/state handling. Preserve the owner's preferences and review changes
to notification behavior explicitly. The larger pipeline design remains parked.
