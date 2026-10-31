-- Select an available crawl partition before running. Change the table if needed.
-- Export the result as CSV, then use backfill.py --athena results.csv --add.
SELECT DISTINCT url
FROM ccindex.ccindex
WHERE crawl = 'CC-MAIN-2026-43'
  AND subset = 'warc'
  AND fetch_status = 200
  AND url_host_name IN (
    'boards.greenhouse.io',
    'job-boards.greenhouse.io',
    'job-boards.eu.greenhouse.io'
  );
