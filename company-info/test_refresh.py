"""Public identity, cache lifetime, and partial-progress contracts."""
from datetime import datetime, timedelta, timezone
import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
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


LOGO = "https://s101-recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/logos/400/153/310/original/logo.png?1769954007"


def http_error(code):
    return urllib.error.HTTPError("https://job-boards.greenhouse.io/x", code, "status", {}, None)


class GreenhouseProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "companies.json"
        self.now = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def test_only_logos_uploaded_to_greenhouse_hosted_boards_are_accepted(self):
        accepted = [f'<meta property="og:image" content="{LOGO}">',
                    f'<meta content="{LOGO.replace("?", "?a=1&amp;b=")}" property="og:image">',
                    '<meta property="og:image" content="https://recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/logos/000/001/452/original/b.png">']
        for page in accepted:
            with self.subTest(page=page):
                self.assertTrue(refresh.board_logo(page).startswith("https://"))
        self.assertEqual(refresh.board_logo(accepted[1]), LOGO.replace("?", "?a=1&b="))
        rejected = ['<meta property="og:image" content="https://cdn.phenompeople.com/HELLGLOBAL/images/banner.jpg">',
                    f'<meta property="og:image" content="{LOGO.replace("https://", "http://")}">',
                    f'<meta property="og:image" content="{LOGO.replace(".greenhouse.io/", ".greenhouse.io.example.com/")}">',
                    '<meta property="og:image" content="https://recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/covers/x.png">',
                    f'<meta name="twitter:image" content="{LOGO}">', ""]
        for page in rejected:
            with self.subTest(page=page):
                self.assertEqual(refresh.board_logo(page), "")

    def test_hosted_page_never_follows_a_redirect_to_another_site(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(301)
                self.send_header("Location", "/company-careers-site")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            refresh.hosted_page(f"http://127.0.0.1:{server.server_port}/token")
        self.assertEqual(caught.exception.code, 301)
        self.assertEqual(seen, ["/token"])

    def test_eu_host_is_a_fallback_and_a_redirect_means_no_logo(self):
        page = f'<meta property="og:image" content="{LOGO}">'
        with patch.object(refresh, "hosted_page", side_effect=[http_error(404), page]) as get:
            self.assertEqual(refresh.hosted_logo("parloa"), LOGO)
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         ["https://job-boards.greenhouse.io/parloa", "https://job-boards.eu.greenhouse.io/parloa"])
        with patch.object(refresh, "hosted_page", side_effect=http_error(302)) as get:
            self.assertEqual(refresh.hosted_logo("hellofresh"), "")
        self.assertEqual(get.call_count, 1)
        with patch.object(refresh, "hosted_page", side_effect=[http_error(404), http_error(404)]):
            self.assertEqual(refresh.hosted_logo("gone"), "")
        with patch.object(refresh, "hosted_page", side_effect=http_error(503)), self.assertRaises(urllib.error.HTTPError):
            refresh.hosted_logo("outage")

    def test_greenhouse_profile_uses_public_board_name_intro_and_hosted_logo(self):
        body = json.dumps({"name": " Braze ", "content": "<p>About Braze</p>"}).encode()
        with patch.object(refresh.radar, "request", side_effect=lambda url, parse: parse(body)) as get, \
                patch.object(refresh, "hosted_logo", return_value=LOGO) as logo:
            profile = refresh.fetch_company("greenhouse:braze")
        self.assertEqual(get.call_args.args[0], "https://boards-api.greenhouse.io/v1/boards/braze")
        logo.assert_called_once_with("braze")
        self.assertEqual(profile, {"name": "Braze", "board_introduction_html": "<p>About Braze</p>", "website": "",
                                   "careers_url": "", "logo_url": LOGO, "canonical_slug": "braze",
                                   "source": refresh.GREENHOUSE_BOARD})
        for payload in ({"name": "  ", "content": ""}, {"content": "<p>x</p>"}, []):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                refresh.parse_board(json.dumps(payload).encode())

    def test_greenhouse_outage_defers_only_greenhouse_profiles(self):
        with patch.object(refresh, "fetch_greenhouse_profile", side_effect=http_error(429)) as greenhouse, \
                patch.object(refresh, "fetch_profile", return_value={"name": "Ashby company"}):
            report = refresh.refresh(["alpha", "greenhouse:a", "greenhouse:b", "zeta"], {}, self.path, delay=0, now=self.now)
        self.assertEqual(greenhouse.call_count, 1)
        self.assertEqual(report["updated"], ["alpha", "zeta"])
        self.assertEqual(report["deferred"], ["greenhouse:b"])
        self.assertEqual(set(json.loads(self.path.read_text())), {"alpha", "zeta"})

    def test_catalog_selection_includes_greenhouse_but_not_workable(self):
        catalog = Path(self.temp.name) / "catalog.json"
        catalog.write_text(json.dumps({"n8n": {}, "greenhouse:braze": {}, "workable:0f3c": {}}))
        with patch.object(refresh, "refresh", return_value={"updated": [], "cached": [], "failed": [], "deferred": []}) as run:
            refresh.main(["--catalog", str(catalog), "--cache", str(self.path), "--report", str(Path(self.temp.name) / "r.json")])
        self.assertEqual(run.call_args.args[0], ["greenhouse:braze", "n8n"])


if __name__ == "__main__":
    unittest.main()
