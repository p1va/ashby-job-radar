"""Provider contracts and delivery recovery, without live network or email."""
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import ashby_radar as radar
import sources

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


class FrozenTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def greenhouse(**changes):
    return {"id": 42, "title": "Product Manager", "company_name": "Actual Company",
            "location": {"name": "Berlin"}, "first_published": "2026-10-30T10:00:00Z",
            "absolute_url": "https://example.com/role?gh_jid=42", **changes}


def workable(identifier="job-1", **changes):
    return {"id": identifier, "title": "Product Manager", "state": "published", "workplace": "hybrid",
            "created": "2026-10-30T10:00:00Z", "location": {"city": "Berlin", "countryName": "Germany"},
            "locations": ["Berlin, Germany"], "description": "<p>Build tools.</p>",
            "requirementsSection": "<p>Fluent in German and English.</p>",
            "benefitsSection": "<p>Flexible hours.</p>", "url": "https://jobs.workable.com/view/" + identifier,
            "company": {"id": "company-uuid", "title": "Actual Company", "website": "https://example.com/"}, **changes}


class AdapterTests(unittest.TestCase):
    def test_names_are_backward_compatible_and_hosts_are_checked(self):
        cases = {"Flock%20Safety": "flock safety", "ashby:Acme": "acme",
                 "https://jobs.ashbyhq.com/Acme/job": "acme", "greenhouse:N26": "greenhouse:n26",
                 "https://job-boards.eu.greenhouse.io/gropyus/jobs/42": "greenhouse:gropyus",
                 "https://boards.greenhouse.io/embed/job_board?for=N26": "greenhouse:n26"}
        for raw, expected in cases.items():
            self.assertEqual(sources.board_key(raw), expected)
        for raw in ["https://jobs.ashbyhq.com.evil.test/acme", "https://example.com/acme",
                    "https://job-boards.greenhouse.io/embed/job_board", "greenhouse:a/b", "lever:acme",
                    "workable:acme", "https://apply.workable.com/acme", "acme%2Fother"]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sources.board_key(raw)
        self.assertEqual(sources.enabled({}), ["ashby"])
        for values in [[], "ashby", ["unknown"], [None]]:
            with self.assertRaises(ValueError):
                sources.enabled({"sources": values})

    def test_greenhouse_returns_nonmatching_jobs_and_ignores_office_assignments(self):
        engineer = greenhouse(id=43, title="Software Engineer")
        london = greenhouse(id=44, location={"name": "London"}, offices=[{"location": "Berlin"}])
        jobs = [greenhouse(), engineer, london]
        def request(url, parse):
            detailed = "content=true" in url
            payload = [{**j, "content": "&lt;p&gt;Fluent in German.&lt;/p&gt;"} for j in jobs] if detailed else jobs
            return parse(json.dumps({"jobs": payload, "meta": {"total": 3}}).encode())
        normalized = sources.fetch_greenhouse("greenhouse:sample", request, lambda j: j["title"] == "Product Manager")
        self.assertEqual(len(normalized), 3)
        self.assertTrue(radar.has_germany([normalized[1]], CONFIG))
        self.assertIsNone(radar.match_location(normalized[2], CONFIG))
        self.assertEqual(normalized[0]["id"], "greenhouse:42")
        self.assertEqual(normalized[0]["descriptionPlain"], "Fluent in German.")
        self.assertTrue(radar.german_required(normalized[0], CONFIG))

    def test_greenhouse_skips_detail_without_candidates_and_keeps_unknown_date(self):
        with patch("builtins.print"):
            def request(url, parse):
                self.assertNotIn("content=true", url)
                return parse(json.dumps({"jobs": [greenhouse(first_published=None, updated_at=NOW.isoformat())]}).encode())
            jobs = sources.fetch_greenhouse("greenhouse:sample", request, lambda j: False)
        self.assertIsNone(jobs[0]["publishedAt"])

    def test_required_detail_failure_is_not_a_successful_board(self):
        calls = 0
        def request(url, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise urllib.error.HTTPError(url, 503, "unavailable", {}, None)
            return kwargs["parse"](json.dumps({"jobs": [greenhouse()]}).encode())
        with patch.object(radar, "request", side_effect=request):
            _, jobs, error = radar.fetch_board("greenhouse:sample", CONFIG)
        self.assertIsNone(jobs)
        self.assertEqual(error, "HTTP 503")

    def test_workable_walk_keeps_native_city_ids_and_full_requirements(self):
        first, second = workable(), workable("job-2", location={"city": "Munich", "countryName": "Germany"})
        pages = [{"jobs": [first], "totalSize": 2, "nextPageToken": "next"},
                 {"jobs": [first, second], "totalSize": 2}]
        def request(url, parse):
            return parse(json.dumps(pages.pop(0)).encode())
        boards = sources.fetch_workable(request, delay=0)
        self.assertEqual([j["id"] for j in boards["workable:company-uuid"]], ["workable:job-1", "workable:job-2"])
        self.assertIn("Flexible hours", boards["workable:company-uuid"][0]["descriptionPlain"])
        self.assertTrue(radar.german_required(boards["workable:company-uuid"][0], CONFIG))
        self.assertEqual(boards["workable:company-uuid"][0]["secondaryLocations"], [])

    def test_workable_rejects_partial_malformed_and_looping_scans(self):
        for page in [{"jobs": [], "totalSize": 5}, {"jobs": [], "nextPageToken": "again"},
                     {"jobs": [None]}, {"error": "unavailable"}, {"jobs": [], "totalSize": "0"}]:
            with self.subTest(page=page), self.assertRaises(ValueError):
                sources.fetch_workable(lambda *a, **k: page, delay=0)
        with self.assertRaises(ValueError):
            sources.fetch_workable(lambda *a, **k: {"jobs": [workable()], "nextPageToken": "again"}, delay=0)
        self.assertEqual(sources.fetch_workable(lambda *a, **k: {"jobs": [], "totalSize": 0}), {})


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cfg = {**CONFIG, "sources": ["workable"], "harvest": {"enabled": False}, "discovery_queries": []}
        self.config = self.root / "config.json"
        self.registry = self.root / "boards.json"
        self.seen = self.root / "seen.json"
        self.companies = self.root / "companies.txt"
        self.config.write_text(json.dumps(self.cfg))
        self.registry.write_text("{}")
        self.seen.write_text(json.dumps({"_last_run": "2026-10-29T08:00:00+00:00"}))
        self.companies.write_text("")
        self.args = ["--config", str(self.config), "--registry", str(self.registry), "--seen", str(self.seen),
                     "--companies", str(self.companies), "--out", str(self.root / "output")]

    def run_radar(self, result, *flags):
        with patch.object(radar, "datetime", FrozenTime), patch.object(radar, "fetch_workable", side_effect=result), \
             patch.object(radar, "fetch_board", side_effect=AssertionError("unexpected board request")), \
             patch.object(radar, "send_email", return_value=True), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            return radar.main(self.args + list(flags))

    def jobs(self):
        key, job = sources.workable_job(workable())
        return {key: [job]}

    def test_first_failure_survives_ashby_progress_and_no_email_never_delivers(self):
        self.assertEqual(self.run_radar(OSError("offline"), "--no-email"), 1)
        initial = json.loads(self.registry.read_text())["_meta"]["workable"]
        self.seen.write_text(json.dumps({"_last_run": NOW.isoformat()}))
        before = self.seen.read_bytes()
        self.assertEqual(self.run_radar(lambda: self.jobs(), "--no-email"), 0)
        self.assertEqual(json.loads((self.root / "output/run.json").read_text())["matches"], 1)
        self.assertEqual(json.loads(self.registry.read_text())["_meta"]["workable"], initial)
        self.assertEqual(self.seen.read_bytes(), before)

    def test_success_records_native_id_and_advances_only_provider_metadata(self):
        self.assertEqual(self.run_radar(lambda: self.jobs()), 0)
        registry = json.loads(self.registry.read_text())
        self.assertEqual(set(registry), {"_meta"})
        self.assertEqual(registry["_meta"]["workable"], {"checked": NOW.isoformat()})
        self.assertIn("workable:job-1", json.loads(self.seen.read_text()))

    def test_valid_empty_scan_is_success_and_dry_run_preserves_every_input(self):
        self.assertEqual(self.run_radar(lambda: {}), 0)
        self.assertEqual(json.loads(self.registry.read_text())["_meta"]["workable"]["checked"], NOW.isoformat())
        before = self.registry.read_bytes(), self.seen.read_bytes()
        self.assertEqual(self.run_radar(lambda: self.jobs(), "--dry-run"), 0)
        self.assertEqual((self.registry.read_bytes(), self.seen.read_bytes()), before)

    def test_smtp_failure_and_overflow_keep_provider_eligibility(self):
        with patch.object(radar, "send_email", side_effect=OSError("SMTP unavailable")):
            # The inner runner normally supplies a successful sender, so exercise
            # main directly here with just network/time replaced.
            with patch.object(radar, "datetime", FrozenTime), patch.object(radar, "fetch_workable", return_value=self.jobs()), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(radar.main(self.args), 1)
        state = json.loads(self.registry.read_text())["_meta"]["workable"]
        self.assertNotIn("checked", state)
        self.assertNotIn("workable:job-1", json.loads(self.seen.read_text()))
        self.config.write_text(json.dumps({**self.cfg, "max_results": 1}))
        both = self.jobs()
        both["workable:company-uuid"].append(sources.workable_job(workable("job-2"))[1])
        self.assertEqual(self.run_radar(lambda: both), 0)
        self.assertNotIn("checked", json.loads(self.registry.read_text())["_meta"]["workable"])


if __name__ == "__main__":
    unittest.main()
