"""Catalog contracts: current matches, readable descriptions, and safe publication."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("catalog_build", Path(__file__).with_name("build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"title_include": ["product"], "title_exclude": [],
            "cities": [{"label": "Berlin", "aliases": ["berlin"]}], "country_terms": ["germany"],
            "focus_cities": ["Berlin"], "german_required_phrases": [], "max_results": 1}), encoding="utf-8")
        self.registry = self.root / "boards.json"
        self.registry.write_text("{}", encoding="utf-8")
        self.companies = self.root / "companies.txt"
        self.companies.write_text("sample\n", encoding="utf-8")
        self.out = self.root / "output"

    def job(self, index=0, **changes):
        return {"id": str(index), "title": "Product Manager <example>", "isListed": True,
                "location": "Berlin", "workplaceType": "Hybrid", "publishedAt": "2020-01-01T00:00:00Z",
                "jobUrl": f"https://jobs.ashbyhq.com/sample/{index}",
                "descriptionPlain": "Build accessible tools with Python.", **changes}

    def args(self):
        return ["--config", str(self.config), "--registry", str(self.registry),
                "--companies", str(self.companies), "--out", str(self.out)]

    def test_all_current_matches_without_history_cutoff_or_digest_cap(self):
        fixtures = self.root / "boards"
        fixtures.mkdir()
        jobs = [self.job(i) for i in range(125)]
        jobs += [self.job(125, publishedAt=None), self.job(126, isListed=False),
                 self.job(127, title="Engineer"), self.job(128, location="London")]
        (fixtures / "sample.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
        # Sent history is deliberately never an input, even if every ID is seen.
        seen = self.root / "seen.json"
        seen.write_text(json.dumps({str(i): "2026-10-26" for i in range(129)}), encoding="utf-8")
        before = {p: p.read_bytes() for p in [seen, self.config, self.registry, self.companies]}
        self.assertEqual(build.main(self.args() + ["--fixtures", str(fixtures)]), 0)
        payload = json.loads(next((self.out / "site").glob("jobs-*.json")).read_text())
        self.assertEqual(len(payload["jobs"]), 126)
        self.assertEqual(payload["jobs"][-1]["published"], None)
        self.assertEqual(payload["jobs"][0]["description"], jobs[0]["descriptionPlain"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        page = (self.out / "site/index.html").read_text()
        self.assertNotIn("{{", page)
        for asset in ["app-", "styles-", "jobs-"]:
            self.assertIn("./" + asset, page)

    def test_descriptions_preserve_text_and_remove_active_html(self):
        self.assertEqual(build.description({"descriptionPlain": "Plain text", "descriptionHtml": "ignored"}), "Plain text")
        self.assertEqual(build.description({"descriptionHtml": '<h2>About &amp; work</h2><p>Build <b>tools</b>.</p><script>bad()</script><style>bad{}</style><p>Apply.</p>'}), "About & work\n\nBuild tools.\n\nApply.")

    def test_failed_fetch_never_creates_publishable_site(self):
        with patch.object(build.radar, "fetch_board", return_value=("sample", None, "HTTP 429")):
            self.assertEqual(build.main(self.args() + ["--board", "sample"]), 1)
        self.assertFalse((self.out / "site").exists())
        report = json.loads((self.out / "run.json").read_text())
        self.assertEqual(report["boards_failed"], 1)

    def test_dead_board_is_skipped_without_hiding_live_matches(self):
        def fetch(slug):
            return (slug, None, "HTTP 404") if slug == "gone" else (slug, [self.job()], None)
        with patch.object(build.radar, "fetch_board", side_effect=fetch):
            self.assertEqual(build.main(self.args() + ["--board", "gone", "--board", "sample"]), 0)
        report = json.loads((self.out / "run.json").read_text())
        self.assertEqual((report["boards_not_found"], report["roles"]), (1, 1))

    def test_unsafe_application_link_falls_back_to_the_job_url(self):
        matches, _ = build.select_matches({"sample": [self.job(applyUrl="javascript:alert(1)")]}, json.loads(self.config.read_text()), {})
        job = build.public_job(matches[0])
        self.assertEqual(job["applyUrl"], job["url"])

    def test_multiple_locations_keep_labels_without_repeating_addresses(self):
        posting = self.job(address={"postalAddress": {"addressLocality": "Berlin", "addressCountry": "Germany"}},
                           secondaryLocations=[{"location": "Munich", "address": {"postalAddress": {"addressLocality": "Munich"}}}])
        matches, _ = build.select_matches({"sample": [posting]}, json.loads(self.config.read_text()), {})
        self.assertEqual(build.public_job(matches[0])["locations"], ["Berlin", "Munich"])

    def test_company_lookup_enriches_names_and_features_without_network(self):
        matches, _ = build.select_matches({"sample": [self.job()]}, json.loads(self.config.read_text()), {})
        jobs = [build.public_job(job) for job in matches]
        profiles = {"sample": {"name": "Actual Brand", "website": "https://example.com/",
                               "logo_url": "javascript:bad()", "timezone": "Europe/Madrid",
                               "board_introduction_html": "<p>We build <b>tools</b>.</p><script>bad()</script>"}}
        build.write_site(self.out, jobs, {"generated_at": "2026-10-26T00:00:00+00:00"}, profiles, ["sample"])
        companies = json.loads((self.out / "companies.json").read_text())
        self.assertEqual(companies["sample"], {"name": "Actual Brand", "website": "https://example.com/",
                        "logoUrl": "", "introduction": "We build tools.", "featured": True, "roles": 1})
        payload = json.loads(next(self.out.glob("jobs-*.json")).read_text())
        self.assertEqual(payload["companies"], companies)
        self.assertEqual(payload["jobs"][0]["companyName"], "Actual Brand")
        self.assertEqual(payload["jobs"][0]["location"], "Berlin")

    def test_missing_company_profile_keeps_the_job_and_slug_name(self):
        matches, _ = build.select_matches({"sample": [self.job()]}, json.loads(self.config.read_text()), {})
        build.write_site(self.out, [build.public_job(matches[0])], {"generated_at": "2026-10-26T00:00:00+00:00"})
        companies = json.loads((self.out / "companies.json").read_text())
        self.assertEqual(companies["sample"]["name"], "Sample")
        self.assertEqual(companies["sample"]["roles"], 1)
        self.assertFalse(companies["sample"]["featured"])

    def test_workable_failure_cannot_publish_an_ashby_only_partial_catalog(self):
        config = json.loads(self.config.read_text())
        self.config.write_text(json.dumps({**config, "sources": ["ashby", "workable"]}))
        with patch.object(build.radar, "fetch_board", return_value=("sample", [self.job()], None)), \
             patch.object(build.radar, "fetch_workable", side_effect=ValueError("incomplete cursor walk")):
            self.assertEqual(build.main(self.args()), 1)
        self.assertFalse((self.out / "site").exists())
        self.assertEqual(json.loads((self.out / "run.json").read_text())["boards_failed"], 1)

    def test_new_provider_names_source_and_safe_company_details_are_exported(self):
        matches, _ = build.select_matches({"workable:uuid": [self.job(
            id="workable:job", source="workable", companyName="Actual Company",
            companyWebsite="https://example.com/", companyLogoUrl="javascript:bad()",
            companyIntroduction="We build tools.")]}, json.loads(self.config.read_text()), {})
        build.write_site(self.out, [build.public_job(matches[0])], {"generated_at": "2026-10-31T00:00:00+00:00"})
        payload = json.loads(next(self.out.glob("jobs-*.json")).read_text())
        self.assertEqual(payload["jobs"][0]["source"], "workable")
        self.assertEqual(payload["jobs"][0]["companyName"], "Actual Company")
        self.assertEqual(payload["companies"]["workable:uuid"]["website"], "https://example.com/")
        self.assertEqual(payload["companies"]["workable:uuid"]["logoUrl"], "")

    def test_valid_empty_workable_result_builds_an_empty_catalog_without_state_writes(self):
        config = json.loads(self.config.read_text())
        self.config.write_text(json.dumps({**config, "sources": ["workable"]}))
        before = self.registry.read_bytes()
        with patch.object(build.radar, "fetch_workable", return_value={}):
            self.assertEqual(build.main(self.args()), 0)
        self.assertTrue((self.out / "site/index.html").exists())
        self.assertEqual(self.registry.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
