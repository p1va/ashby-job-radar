import contextlib
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("catch_up", Path(__file__).with_name("preview.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
NOW = datetime.now(timezone.utc)
CONFIG = {**json.loads((preview.ROOT / "config.json").read_text()), "sources": ["ashby"]}


def job(key, **extra):
    return {"id": key, "title": "Product Manager", "location": "Berlin, Germany", "isListed": True,
            "publishedAt": (NOW - timedelta(days=300)).isoformat(), "workplaceType": "Hybrid",
            "jobUrl": f"https://jobs.ashbyhq.com/example/{key}", **extra}


class SelectionTests(unittest.TestCase):
    def test_ignores_date_cutoffs_retention_and_result_cap(self):
        cfg = {**CONFIG, "max_results": 1, "forget_after_days": 1, "lookback_days": 1}
        jobs = [job(str(i)) for i in range(130)]
        original = copy.deepcopy(jobs)
        matches, counts = preview.select_matches({"example": jobs}, cfg, {"_last_run": NOW.isoformat()})
        self.assertEqual((len(matches), counts["matching_jobs"]), (130, 130))
        self.assertEqual(jobs, original)

    def test_skips_seen_ids_and_seen_urls_without_mutating_history(self):
        jobs = [job("id-seen"), job("url-seen"), job("unseen")]
        seen = {"id-seen": "2026-10-25", jobs[1]["jobUrl"]: "2026-10-25", "_last_run": NOW.isoformat()}
        original = dict(seen)
        matches, counts = preview.select_matches({"example": jobs}, CONFIG, seen)
        self.assertEqual([j["id"] for j in matches], ["unseen"])
        self.assertEqual(counts["already_seen"], 2)
        self.assertEqual(seen, original)

    def test_keeps_title_location_focus_and_listing_rules(self):
        jobs = [job("good"), job("closed", isListed=False), job("wrong-title", title="Pastry Chef"),
                job("wrong-place", location="London, UK"), job("excluded", title="Product Manager Intern"),
                job("remote", location="Germany", workplaceType="Remote"),
                job("hybrid-country", location="Germany", workplaceType="Hybrid")]
        cfg = {**CONFIG, "title_exclude": ["intern"]}
        matches, _ = preview.select_matches({"example": jobs}, cfg, {})
        self.assertEqual({j["id"] for j in matches}, {"good", "remote"})

    def test_duplicate_jobs_across_boards_are_counted_once(self):
        matches, counts = preview.select_matches({"first": [job("same")], "second": [job("same")]}, CONFIG, {})
        self.assertEqual((len(matches), counts["duplicate_matches"]), (1, 1))

    def test_missing_or_invalid_date_is_included_and_shown_honestly(self):
        jobs = [job("missing", publishedAt=None), job("bad", publishedAt="unknown"),
                job("old", publishedAt="2020-01-01T00:00:00Z")]
        matches, counts = preview.select_matches({"example": jobs}, CONFIG, {})
        self.assertEqual((len(matches), counts["matches_without_date"]), (3, 2))
        self.assertEqual(matches[0]["id"], "old")
        self.assertIsNone(preview.public_row(matches[1])["published"])
        self.assertIn("Date not provided", preview.job_meta(matches[1]))

    def test_recent_selection_matches_existing_radar_filters(self):
        jobs = [job("recent", publishedAt=(NOW - timedelta(hours=1)).isoformat()),
                job("seen", publishedAt=(NOW - timedelta(hours=1)).isoformat()),
                job("other", title="Pastry Chef")]
        seen = {"seen": "2026-10-25", "_last_run": (NOW - timedelta(hours=6)).isoformat()}
        regular = preview.radar.curate({"example": copy.deepcopy(jobs)}, CONFIG, seen, NOW)
        catchup, _ = preview.select_matches({"example": jobs}, CONFIG, seen)
        self.assertEqual({j["id"] for j in regular}, {j["id"] for j in catchup})


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {k: self.root / name for k, name in {"config": "config.json", "seen": "seen.json",
                      "registry": "boards.json", "companies": "companies.txt", "embedded": "embedded.txt"}.items()}
        self.paths["config"].write_text(json.dumps({**CONFIG, "max_results": 1}))
        self.paths["seen"].write_text(json.dumps({"seen": "2026-10-25", "_last_run": NOW.isoformat()}))
        self.paths["registry"].write_text('{"example":{"de":true,"checked":"2026-10-25"}}')
        self.paths["companies"].write_text("EXAMPLE\n")
        self.paths["embedded"].write_text("example\n")
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        self.out = self.root / "output"

    def invoke(self, *flags):
        argv = [part for key, path in self.paths.items() for part in ("--" + key, str(path))]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return preview.main(argv + ["--out", str(self.out), *flags])

    def test_full_report_and_inputs_unchanged_including_last_run(self):
        body = {"jobs": [job("seen")] + [job(str(i)) for i in range(125)]}
        (self.fixtures / "example.json").write_text(json.dumps(body))
        before = {k: p.read_bytes() for k, p in self.paths.items()}
        with patch.object(preview.radar, "send_email", side_effect=AssertionError("email")), \
             patch.object(preview.radar, "discover_slugs", side_effect=AssertionError("search")), \
             patch.object(preview.radar, "fetch_board", side_effect=AssertionError("network")):
            self.assertEqual(self.invoke("--fixtures", str(self.fixtures)), 0)
        self.assertEqual(before, {k: p.read_bytes() for k, p in self.paths.items()})
        report = json.loads((self.out / "run.json").read_text())
        self.assertEqual((report["matches"], report["already_seen"], report["boards_requested"]), (125, 1, 1))
        self.assertEqual(len(json.loads((self.out / "matches.json").read_text())), 125)
        self.assertEqual((self.out / "digest.md").read_text().count("### "), 125)
        self.assertEqual((self.out / "digest.html").read_text().count("<article>"), 125)
        self.assertEqual((self.out / "summary.md").read_text().count("### "), 20)

    def test_live_sources_deduplicate_and_cache_replays(self):
        self.paths["embedded"].write_text("example\nextra\n")
        before = {k: p.read_bytes() for k, p in self.paths.items()}
        with patch.object(preview.radar, "fetch_board", side_effect=lambda slug: (slug, [job(slug)], None)) as fetch:
            self.assertEqual(self.invoke("--cache"), 0)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(before, {k: p.read_bytes() for k, p in self.paths.items()})
        cache = self.out / "boards"
        expected = json.loads((self.out / "matches.json").read_text())
        self.out = self.root / "replay"
        with patch.object(preview.radar, "fetch_board", side_effect=AssertionError("network")):
            self.assertEqual(self.invoke("--fixtures", str(cache)), 0)
        self.assertEqual(json.loads((self.out / "matches.json").read_text()), expected)

    def test_partial_failure_keeps_report_and_state(self):
        self.paths["companies"].write_text("example\nmissing\nunavailable\n")
        before = {k: p.read_bytes() for k, p in self.paths.items()}
        def fetch(slug):
            return (slug, [job("match")], None) if slug == "example" else (slug, None, "HTTP 404" if slug == "missing" else "HTTP 503")
        with patch.object(preview.radar, "fetch_board", side_effect=fetch):
            self.assertEqual(self.invoke(), 1)
        report = json.loads((self.out / "run.json").read_text())
        self.assertEqual((report["matches"], report["boards_not_found"], report["boards_failed"]), (1, 1, 1))
        self.assertIn("results are incomplete", (self.out / "digest.html").read_text())
        self.assertEqual(before, {k: p.read_bytes() for k, p in self.paths.items()})

    def test_missing_or_corrupt_seen_stops_before_fetch(self):
        for body in (None, "{broken", "[]"):
            if body is None:
                self.paths["seen"].unlink()
            else:
                self.paths["seen"].write_text(body)
            with patch.object(preview.radar, "fetch_board", side_effect=AssertionError("network")), self.assertRaises(SystemExit):
                self.invoke()
            self.assertFalse(self.out.exists())

    def test_bad_fixture_is_failure_and_not_an_empty_board_success(self):
        (self.fixtures / "example.json").write_text('{"error":"unavailable"}')
        self.assertEqual(self.invoke("--fixtures", str(self.fixtures)), 1)
        report = json.loads((self.out / "run.json").read_text())
        self.assertEqual((report["boards_ok"], report["matches"]), (0, 0))
        self.assertIn("No boards could be fetched", (self.out / "digest.md").read_text())


if __name__ == "__main__":
    unittest.main()
