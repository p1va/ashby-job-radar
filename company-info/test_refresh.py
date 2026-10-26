"""Public identity, cache lifetime, and partial-progress contracts."""
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

SPEC = importlib.util.spec_from_file_location("company_refresh", Path(__file__).with_name("refresh.py"))
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "companies.json"
        self.now = datetime(2026, 10, 26, tzinfo=timezone.utc)

    def response(self, **fields):
        return json.dumps({"data": {"organizationFromHostedJobsPageName": {
            "organizationId": "org-1", "name": "Actual Company", "publicWebsite": "https://example.com/",
            "timezone": "Europe/Madrid", "theme": {"logoSquareImageUrl": "https://example.com/logo.png"},
            **fields}}}).encode()

    def test_public_graphql_is_json_post_with_gzip(self):
        response = io.BytesIO(gzip.compress(self.response()))
        response.headers = {"Content-Encoding": "gzip"}
        with patch.object(refresh.radar.urllib.request, "urlopen", return_value=response) as open_url:
            profile = refresh.fetch_profile("sample")
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Accept-encoding"), "gzip")
        self.assertEqual(json.loads(request.data)["variables"], {"slug": "sample"})
        self.assertEqual(profile["name"], "Actual Company")
        self.assertEqual(profile["timezone"], "Europe/Madrid")
        self.assertNotIn("location", profile)

    def test_existing_transport_still_sends_get_without_a_body(self):
        response = io.BytesIO(b'{"ok": true}')
        response.headers = {}
        with patch.object(refresh.radar.urllib.request, "urlopen", return_value=response) as open_url:
            self.assertEqual(refresh.radar.request("https://example.com", parse=json.loads), {"ok": True})
        self.assertEqual(open_url.call_args.args[0].get_method(), "GET")
        self.assertIsNone(open_url.call_args.args[0].data)

    def test_http_200_graphql_errors_and_missing_org_are_not_profiles(self):
        for payload in ({"errors": [{"message": "Schema changed"}]}, {"data": {"organizationFromHostedJobsPageName": None}}, []):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                refresh.parse_profile(json.dumps(payload).encode())

    def test_recent_profiles_make_no_company_requests(self):
        cache = {"sample": {"name": "Saved", "fetched_at": (self.now - timedelta(days=29)).isoformat()}}
        with patch.object(refresh, "fetch_profile", side_effect=AssertionError("network forbidden")):
            report = refresh.refresh(["sample"], cache, self.path, now=self.now)
        self.assertEqual(report["cached"], ["sample"])
        self.assertEqual(report["updated"], [])
        self.assertTrue(refresh.needs_refresh({"name": "Saved", "fetched_at": (self.now - timedelta(days=30)).isoformat()}, self.now, 30))
        self.assertTrue(refresh.needs_refresh({"name": "Saved", "fetched_at": "bad"}, self.now, 30))

    def test_success_is_saved_before_a_later_failure_and_old_records_survive(self):
        cache = {"bad": {"name": "Last good"}, "no-longer-active": {"name": "Keep"}}
        def fetch(slug):
            if slug == "bad":
                self.assertEqual(json.loads(self.path.read_text())["good"]["name"], "New name")
                raise ValueError("No organization")
            return {"name": "New name"}
        with patch.object(refresh, "fetch_profile", side_effect=fetch):
            report = refresh.refresh(["good", "bad"], cache, self.path, delay=0, now=self.now)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["bad"]["name"], "Last good")
        self.assertIn("no-longer-active", saved)
        self.assertEqual(report["updated"], ["good"])
        self.assertEqual(len(report["failed"]), 1)

    def test_rate_limit_outage_or_query_error_stops_the_remaining_batch(self):
        errors = [urllib.error.HTTPError(refresh.GRAPHQL, code, "Unavailable", {}, None) for code in (429, 503)]
        errors.append(refresh.GraphQLError("Schema changed"))
        for error in errors:
            with self.subTest(error=error), patch.object(refresh, "fetch_profile", side_effect=error) as fetch:
                report = refresh.refresh(["first", "later"], {}, self.path, delay=0, now=self.now)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(report["deferred"], ["later"])
            self.assertFalse(self.path.exists())

    def test_force_refreshes_a_recent_profile(self):
        cache = {"sample": {"name": "Old name", "fetched_at": self.now.isoformat()}}
        with patch.object(refresh, "fetch_profile", return_value={"name": "New name"}) as fetch:
            report = refresh.refresh(["sample"], cache, self.path, force=True, now=self.now)
        fetch.assert_called_once_with("sample")
        self.assertEqual(report["updated"], ["sample"])

    def test_non_web_links_are_rejected(self):
        profile = refresh.parse_profile(self.response(publicWebsite="javascript:bad()", theme={"logoSquareImageUrl": "data:fake"}))
        self.assertEqual(profile["website"], "")
        self.assertEqual(profile["logo_url"], "")


if __name__ == "__main__":
    unittest.main()
