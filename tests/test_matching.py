"""Opt-in remote scope and role-background matching using real missed-role shapes."""
import unittest

import ashby_radar as radar


CONFIG = {"title_include": ["product", "operations"], "title_exclude": ["engineer", "internship"],
          "cities": [{"label": "Berlin", "aliases": ["berlin"]}], "focus_cities": ["Berlin"],
          "country_terms": ["germany"], "include_remote_europe": True, "include_remote_global": True,
          "ignore_background_qualifiers": True}


class MatchingTests(unittest.TestCase):
    def test_remote_europe_and_worldwide_pass_city_focus_only_when_enabled(self):
        for location, expected in [("Europe", "Remote Europe"), ("EMEA", "Remote Europe"),
                                   ("Remote, Global", "Remote Worldwide"), ("Worldwide", "Remote Worldwide")]:
            with self.subTest(location=location):
                job = {"location": location, "workplaceType": "Remote"}
                self.assertEqual(radar.match_location(job, CONFIG), expected)
                self.assertTrue(radar.in_focus(expected, "Remote", CONFIG))
                strict = {**CONFIG, "include_remote_europe": False, "include_remote_global": False}
                self.assertIsNone(radar.match_location(job, strict))
                self.assertFalse(radar.in_focus(expected, "Remote", strict))

    def test_structured_eu_evidence_recovers_posthog_without_company_exceptions(self):
        title = "Product Manager, Observability (ex-founder or ex-product engineer)"
        job = {"title": title, "location": "Remote", "workplaceType": "Remote",
               "address": {"postalAddress": {"addressCountry": "US | EU"}}}
        self.assertEqual(radar.match_location(job, CONFIG), "Remote Europe")
        self.assertIsNone(radar.title_rejection(title, CONFIG))
        self.assertEqual(radar.title_rejection(title, {**CONFIG, "ignore_background_qualifiers": False}), "title_excluded")

    def test_remote_only_us_only_and_onsite_europe_are_not_inferred_eligible(self):
        for job in [{"location": "Remote", "workplaceType": "Remote"},
                    {"location": "Remote (US only)", "workplaceType": "Remote", "descriptionPlain": "A global team working anywhere."},
                    {"location": "Europe", "workplaceType": "OnSite"}]:
            with self.subTest(job=job):
                self.assertIsNone(radar.match_location(job, CONFIG))

    def test_role_exclusions_stay_effective_and_background_does_not_create_a_match(self):
        for title in ["Product Engineer (ex-founder)", "Product Manager (Internship)",
                      "Product Manager (Engineer)", "Product Manager (former engineer) - Internship"]:
            with self.subTest(title=title):
                self.assertEqual(radar.title_rejection(title, CONFIG), "title_excluded")
        self.assertEqual(radar.title_rejection("Finance Manager (ex-product engineer)", CONFIG), "title_not_included")
        self.assertIsNone(radar.title_rejection("Product Lead (former engineer)", CONFIG))

    def test_explicit_germany_and_focus_cities_keep_their_existing_labels(self):
        for location in ["Germany", "Berlin"]:
            self.assertEqual(radar.match_location({"location": location, "workplaceType": "Remote"}, CONFIG), location)


if __name__ == "__main__":
    unittest.main()
