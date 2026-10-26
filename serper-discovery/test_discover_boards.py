import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location("discovery", Path(__file__).with_name("discover_boards.py"))
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)
JID = "6195ce2d-dcdd-43d2-8f48-5dc47755afd2"
OTHER = "008d13a4-9159-4390-8005-726e7b9e7237"


class Response(io.BytesIO):
    def __init__(self, body, encoding=""):
        super().__init__(body)
        self.headers = {"Content-Encoding": encoding}


class ParsingTests(unittest.TestCase):
    def test_actual_urls_case_spaces_and_job_ids(self):
        self.assertEqual(discovery.ashby_link(f"https://JOBS.ASHBYHQ.COM/Flock%20Safety/{JID}?utm=x"),
                         ("flock safety", JID))
        self.assertEqual(discovery.ashby_link("https://jobs.ashbyhq.com/Acme/embed?version=2"), ("acme", ""))

    def test_lookalikes_reserved_paths_and_encoded_separators_are_rejected(self):
        for url in ["https://jobs.ashbyhq.com.evil.test/acme", "https://evil.test/jobs.ashbyhq.com/acme",
                    "https://jobs.ashbyhq.com/static/x", "https://jobs.ashbyhq.com/", "https://[",
                    "https://jobs.ashbyhq.com/a%2Fb", "https://jobs.ashbyhq.com/${company_slug}"]:
            with self.subTest(url=url):
                self.assertIsNone(discovery.ashby_link(url))

    def test_json_escaped_html_links_and_sitelinks(self):
        links = discovery.page_links(br'{"url":"https:\/\/jobs.ashbyhq.com\/Flock%20Safety\/embed?version=2"}')
        self.assertEqual({discovery.ashby_link(url)[0] for url in links}, {"flock safety"})
        result = {"link": "a", "sitelinks": {"expanded": [{"link": "b"}], "inline": [{"link": "c"}]}}
        self.assertEqual(list(discovery.result_links(result)), ["a", "b", "c"])
        self.assertEqual(list(discovery.result_links({"sitelinks": None})), [])

    def test_active_job_ids_exclude_unlisted_and_include_secondary_locations(self):
        info = discovery.board_info({"jobs": [
            {"id": JID, "location": "Hamburg", "secondaryLocations": [{"location": "Berlin"}]},
            {"id": OTHER, "isListed": False, "location": "Berlin"}]})
        self.assertEqual((info["jobs"], info["berlin_jobs"], info["job_ids"]), (1, 1, {JID}))
        self.assertEqual(discovery.board_info({"jobs": []})["jobs"], 0)
        for body in [{}, {"jobs": None}, {"jobs": [None]}, []]:
            with self.assertRaises(ValueError):
                discovery.board_info(body)


class RequestTests(unittest.TestCase):
    def test_truncated_gzip_get_retries_complete_body(self):
        body = gzip.compress(b'{"jobs":[]}')
        with patch.object(discovery.urllib.request, "urlopen", side_effect=[Response(body[:-4], "gzip"), Response(body, "gzip")]) as opened, patch.object(discovery.time, "sleep"):
            self.assertEqual(discovery.request("https://example.test"), b'{"jobs":[]}')
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(opened.call_args.args[0].get_header("Accept-encoding"), "gzip")

    def test_paid_search_does_not_retry_or_sleep(self):
        error = urllib.error.HTTPError(discovery.SERPER, 429, "rate limit", {"Retry-After": "10"}, None)
        with patch.object(discovery.urllib.request, "urlopen", side_effect=error) as opened, patch.object(discovery.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                discovery.request(discovery.SERPER, data=b"{}", tries=1)
        self.assertEqual(opened.call_count, 1)
        sleep.assert_not_called()

    def test_permanent_errors_and_long_retry_after_do_not_retry(self):
        for status, headers in [(404, {}), (429, {"Retry-After": "120"})]:
            error = urllib.error.HTTPError("https://example.test", status, "failure", headers, None)
            with patch.object(discovery.urllib.request, "urlopen", side_effect=error) as opened, patch.object(discovery.time, "sleep") as sleep:
                with self.assertRaises(urllib.error.HTTPError):
                    discovery.request("https://example.test")
            self.assertEqual(opened.call_count, 1)
            sleep.assert_not_called()


class MainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / "boards.json"
        self.initial = {"_meta": {"completed_crawls": ["crawl"]}, "Known": {"de": True, "checked": "2026-10-25"}}
        self.registry.write_text(json.dumps(self.initial))
        self.out = self.root / "output"

    def run_main(self, request, *flags, key="test-secret"):
        with patch.dict(os.environ, {"SERPER_API_KEY": key}), patch.object(discovery, "request", side_effect=request) as calls, patch.object(discovery.time, "sleep"), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = discovery.main(["--boards", str(self.registry), "--out", str(self.out), *flags])
        return result, calls

    def search(self, urls):
        return json.dumps({"organic": [{"title": "Role", "position": i, "link": url} for i, url in enumerate(urls, 1)], "credits": 1}).encode()

    def test_known_results_do_not_stop_pagination_add_only_live_new_boards(self):
        before = self.registry.read_bytes()
        def request(url, **kwargs):
            if url == discovery.SERPER:
                page = json.loads(kwargs["data"])["page"]
                return self.search([f"https://jobs.ashbyhq.com/known/{JID}"] if page == 1 else [
                    f"https://jobs.ashbyhq.com/new/{OTHER}", "https://jobs.ashbyhq.com/empty",
                    "https://jobs.ashbyhq.com/gone", f"https://jobs.ashbyhq.com/KNOWN/{JID}"])
            if url.endswith("gone"):
                raise urllib.error.HTTPError(url, 404, "missing", {}, None)
            return json.dumps({"jobs": [] if url.endswith("empty") else [{"id": OTHER, "location": "Berlin"}]}).encode()
        result, calls = self.run_main(request, "--query", "custom", "--add")
        self.assertEqual(result, 0)
        summary = json.loads((self.out / "run.json").read_text())
        self.assertEqual((summary["serper_requests"], summary["credits_reported"], summary["live_boards"]), (2, 2, 3))
        self.assertEqual(summary["added"], ["empty", "new"])
        saved = json.loads(self.registry.read_text())
        self.assertEqual({k: saved[k] for k in self.initial}, json.loads(before))
        self.assertEqual(saved["new"], {"de": None})
        self.assertEqual((self.out / "new-slugs.txt").read_text(), "empty\nnew\n")
        self.assertEqual(sum(call.args[0].endswith("known") for call in calls.call_args_list), 1)
        report = (self.out / "search-results.csv").read_text()
        self.assertIn("not currently listed", report)
        self.assertIn("currently listed", report)

    def test_default_report_only_and_date_filter(self):
        before = self.registry.read_bytes()
        def request(url, **kwargs):
            if url == discovery.SERPER:
                payload = json.loads(kwargs["data"])
                self.assertEqual((payload["gl"], payload["hl"], payload["tbs"]), ("de", "en", "qdr:m"))
                self.assertEqual(kwargs["headers"]["X-API-KEY"], "test-secret")
                return self.search(["https://jobs.ashbyhq.com/new"])
            self.assertNotIn("headers", kwargs)
            return b'{"jobs":[]}'
        result, _ = self.run_main(request, "--query", "custom", "--pages", "1", "--recent", "month")
        self.assertEqual(result, 0)
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertNotIn("test-secret", "".join(p.read_text() for p in self.out.rglob("*.json")))

    def test_search_error_preserves_successful_page_and_reports_partial_run(self):
        def request(url, **kwargs):
            if url == discovery.SERPER:
                if json.loads(kwargs["data"])["page"] == 2:
                    raise urllib.error.HTTPError(url, 402, "credits exhausted", {}, None)
                return self.search(["https://jobs.ashbyhq.com/new"])
            return b'{"jobs":[]}'
        result, _ = self.run_main(request, "--query", "custom", "--add")
        self.assertEqual(result, 1)
        summary = json.loads((self.out / "run.json").read_text())
        self.assertEqual((summary["search_pages"], summary["new_live_boards"]), (1, 1))
        self.assertEqual(len(list((self.out / "searches").glob("*.json"))), 2)
        self.assertEqual(summary["errors"][0]["stage"], "search")
        self.assertIn("new", json.loads(self.registry.read_text()))

    def test_board_failure_is_not_missing_or_added(self):
        def request(url, **kwargs):
            if url == discovery.SERPER:
                return self.search(["https://jobs.ashbyhq.com/broken"])
            raise OSError("offline")
        result, _ = self.run_main(request, "--query", "custom", "--pages", "1", "--add")
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(self.registry.read_text()), self.initial)
        self.assertIn("error", (self.out / "boards.csv").read_text())
        self.assertEqual((self.out / "new-slugs.txt").read_text(), "")

    def test_replay_needs_no_api_key_and_rereads_registry_before_adding(self):
        replay = self.root / "previous/searches"
        replay.mkdir(parents=True)
        (replay / "001.json").write_text(json.dumps({"request": {"q": "saved", "page": 1},
            "response": json.loads(self.search(["https://jobs.ashbyhq.com/new"]))}))
        def request(url, **kwargs):
            self.assertTrue(url.startswith(discovery.ASHBY))
            updated = dict(self.initial, concurrent={"de": False, "checked": "2026-10-26"})
            self.registry.write_text(json.dumps(updated))
            return b'{"jobs":[]}'
        result, calls = self.run_main(request, "--replay", str(replay.parent), "--add", key="")
        self.assertEqual((result, calls.call_count), (0, 1))
        self.assertIn("concurrent", json.loads(self.registry.read_text()))
        self.assertEqual(json.loads((self.out / "run.json").read_text())["serper_requests"], 0)

    def test_corrupt_or_ambiguous_registry_fails_before_paid_search(self):
        for body in ['{broken', '{"Acme":{},"acme":{}}', '[]']:
            self.registry.write_text(body)
            with self.assertRaises(SystemExit) as raised:
                self.run_main(lambda *a, **k: self.fail("request must not happen"))
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(self.registry.read_text(), body)

    def test_follow_external_result_recovers_encoded_slug_and_obeys_cap(self):
        def request(url, **kwargs):
            if url == discovery.SERPER:
                return self.search(["https://example.com/careers", "https://other.com/jobs"])
            if url == "https://example.com/careers":
                return br'<script src="https:\/\/jobs.ashbyhq.com\/Flock%20Safety\/embed?version=2">'
            self.assertTrue(url.endswith("flock%20safety"))
            return b'{"jobs":[]}'
        result, calls = self.run_main(request, "--query", "ashby Berlin", "--pages", "1", "--follow-sites", "1")
        self.assertEqual((result, calls.call_count), (0, 3))
        self.assertEqual((self.out / "new-slugs.txt").read_text(), "flock safety\n")


class RegistryTests(unittest.TestCase):
    def test_failed_atomic_replace_preserves_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "boards.json"
            path.write_text('{"existing":{"de":true}}\n')
            before = path.read_bytes()
            with patch.object(discovery.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    discovery.add_boards(path, ["new"])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(temp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
