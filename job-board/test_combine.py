"""Provider isolation, bounded stale results and fresh-only unseen digest contracts."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build
import combine


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = datetime.now(timezone.utc)
        self.cfg = {"sources": ["ashby", "workable"], "title_include": ["product"], "title_exclude": [],
                    "cities": [{"label": "Berlin", "aliases": ["berlin"]}], "country_terms": ["germany"],
                    "focus_cities": ["Berlin"], "german_required_phrases": []}

    def job(self, source, index=1):
        return {"id": (source + ":" if source != "ashby" else "") + str(index),
                "source": source, "title": "Product Manager", "isListed": True,
                "location": "Berlin", "publishedAt": "2026-10-01T00:00:00Z",
                "jobUrl": f"https://example.com/{source}/{index}", "descriptionPlain": "Build useful things."}

    def collection(self, provider, jobs=None, **changes):
        jobs = [self.job(provider)] if jobs is None else jobs
        company = "sample" if provider == "ashby" else provider + ":sample"
        report = {"generated_at": self.now.isoformat(), "sources": [provider], "source": {},
                  "boards_requested": 1, "boards_failed": 0, "boards_not_found": 0,
                  "boards_ok": 1, "roles": len(jobs), "jobs_read": len(jobs),
                  "failure_budget_exceeded": [], "workable_scan_complete": provider == "workable"}
        report.update(changes)
        data = {"version": 1, "provider": provider, "config_hash": build.config_hash(self.cfg),
                "report": report, "boards": {company: jobs}}
        folder = self.root / ("catalog-source-" + provider)
        folder.mkdir(exist_ok=True)
        (folder / "collection.json").write_text(json.dumps(data))
        return data

    def previous(self, source="workable", hours=1):
        company = source + ":sample"
        matches, _ = build.select_matches({company: [self.job(source)]}, self.cfg, {})
        return {"configHash": build.config_hash(self.cfg), "generatedAt": self.now.isoformat(),
                "sourceStatus": {source: {"state": "fresh", "updatedAt": (self.now-timedelta(hours=hours)).isoformat()}},
                "jobs": [build.public_job(job) for job in matches]}

    def merge(self, previous=None):
        return combine.combine(self.root, self.cfg, {}, previous or {}, self.now, timedelta(hours=48))

    def test_independent_collections_merge_without_refetching(self):
        self.collection("ashby")
        self.collection("workable")
        jobs, boards, report = self.merge()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(set(boards), {"sample", "workable:sample"})
        self.assertFalse(report["partial"])
        self.assertEqual(report["roles_by_source"], {"ashby": 1, "workable": 1})

    def test_one_successful_artifact_can_be_flattened(self):
        self.collection("ashby")
        (self.root/"catalog-source-ashby/collection.json").rename(self.root/"collection.json")
        jobs, _, report = self.merge()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(report["fresh_sources"], ["ashby"])
        self.assertEqual(report["unavailable_sources"], ["workable"])

    def test_duplicate_provider_artifacts_are_rejected(self):
        data = self.collection("ashby")
        (self.root/"collection.json").write_text(json.dumps(data))
        self.collection("workable")
        _, _, report = self.merge()
        self.assertEqual(report["fresh_sources"], ["workable"])

    def test_missing_provider_retains_recent_jobs_and_original_timestamp(self):
        self.collection("ashby")
        previous = self.previous(hours=30)
        previous["sourceStatus"]["workable"]["state"] = "stale"
        jobs, boards, report = self.merge(previous)
        self.assertEqual(len(jobs), 2)
        self.assertNotIn("workable:sample", boards)
        self.assertEqual(report["source_status"]["workable"]["updatedAt"], previous["sourceStatus"]["workable"]["updatedAt"])
        self.assertEqual(report["source_status"]["workable"]["state"], "stale")
        self.assertTrue(report["partial"])

    def test_expired_or_future_fallback_is_omitted(self):
        self.collection("ashby")
        for hours in [49, -1]:
            with self.subTest(hours=hours):
                jobs, _, report = self.merge(self.previous(hours=hours))
                self.assertEqual(len(jobs), 1)
                self.assertEqual(report["source_status"]["workable"]["state"], "unavailable")

    def test_previous_filter_mismatch_cannot_reintroduce_old_preferences(self):
        self.collection("ashby")
        previous = self.previous()
        previous["configHash"] = "different filters"
        jobs, _, _ = self.merge(previous)
        self.assertEqual(len(jobs), 1)

    def test_successful_empty_source_removes_old_roles(self):
        self.collection("ashby")
        self.collection("workable", [])
        jobs, _, report = self.merge(self.previous())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(report["source_status"]["workable"]["state"], "fresh")
        self.assertEqual(report["retained_roles"], 0)

    def test_incomplete_cursor_walk_is_not_accepted_as_a_collection(self):
        self.collection("ashby")
        self.collection("workable", workable_scan_complete=False)
        jobs, _, report = self.merge()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(report["fresh_sources"], ["ashby"])

    def test_incompatible_provenance_or_config_rejects_collection(self):
        self.collection("ashby")
        for changes in [{"source": {"commit": "wrong"}}, {"sources": ["greenhouse"]}]:
            with self.subTest(changes=changes):
                self.collection("workable", **changes)
                jobs, _, report = self.merge()
                self.assertEqual(len(jobs), 1)
                self.assertEqual(report["source_status"]["workable"]["state"], "unavailable")

    def test_collection_cannot_smuggle_a_different_provider(self):
        self.collection("ashby")
        self.collection("workable", [self.job("greenhouse")])
        jobs, _, _ = self.merge()
        self.assertEqual(len(jobs), 1)

    def test_partial_board_budget_is_kept_per_provider(self):
        self.collection("ashby", boards_requested=20, boards_failed=1)
        self.collection("workable")
        _, _, report = self.merge()
        self.assertEqual(report["source_status"]["ashby"]["state"], "partial")
        self.collection("ashby", boards_requested=19, boards_failed=1)
        _, _, report = self.merge()
        self.assertEqual(report["source_status"]["ashby"]["state"], "unavailable")

    def args(self):
        cfg = self.root / "config.json"
        cfg.write_text(json.dumps(self.cfg))
        seen = self.root / "seen.json"
        seen.write_text(json.dumps({"1": "2026-10-01"}))
        return ["--collections", str(self.root), "--config", str(cfg), "--seen", str(seen), "--out", str(self.root/"output")]

    def test_all_failed_does_not_create_publishable_site(self):
        previous = self.root / "previous.json"
        previous.write_text(json.dumps(self.previous()))
        self.assertEqual(combine.main(self.args() + ["--previous", str(previous)]), 1)
        self.assertFalse((self.root/"output/site").exists())
        self.assertTrue((self.root/"output/run.json").exists())

    def test_catalog_keeps_seen_and_stale_jobs_but_digest_only_has_fresh_unseen(self):
        self.collection("ashby", [self.job("ashby", 1), self.job("ashby", 2)])
        previous = self.root / "previous.json"
        previous.write_text(json.dumps(self.previous()))
        self.assertEqual(combine.main(self.args() + ["--previous", str(previous)]), 0)
        out = self.root/"output"
        site = json.loads(next((out/"site").glob("jobs-*.json")).read_text())
        digest = json.loads((out/"digest/matches.json").read_text())
        self.assertEqual(len(site["jobs"]), 3)
        self.assertEqual([job["id"] for job in digest], ["2"])
        self.assertTrue(site["partial"])
        self.assertEqual(site["sourceStatus"]["workable"]["state"], "stale")
        self.assertIn("Sources not refreshed: workable", (out/"digest/digest.md").read_text())
        self.assertEqual(json.loads((self.root/"seen.json").read_text()), {"1": "2026-10-01"})

    def test_unreadable_previous_site_does_not_block_a_healthy_source(self):
        self.collection("ashby")
        with patch.object(combine, "load_previous", side_effect=OSError("offline")):
            self.assertEqual(combine.main(self.args() + ["--previous-url", "https://example.com/"]), 0)
        data = json.loads(next((self.root/"output/site").glob("jobs-*.json")).read_text())
        self.assertTrue(data["partial"])
        self.assertEqual(data["sourceStatus"]["workable"]["state"], "unavailable")

    def test_workable_collect_only_exports_no_site_or_state_and_keeps_native_ids(self):
        cfg = self.root / "config.json"
        cfg.write_text(json.dumps(self.cfg))
        registry = self.root / "boards.json"
        registry.write_text("{}")
        out = self.root / "collected"
        args = ["--config", str(cfg), "--registry", str(registry), "--source", "workable", "--collect-only", "--out", str(out)]
        with patch.object(build.radar, "fetch_workable", return_value={"workable:sample": [self.job("workable")]}):
            self.assertEqual(build.main(args), 0)
        data = json.loads((out/"collection.json").read_text())
        self.assertEqual(data["boards"]["workable:sample"][0]["id"], "workable:1")
        self.assertFalse(any(key.startswith("_") for key in data["boards"]["workable:sample"][0]))
        self.assertEqual(registry.read_text(), "{}")
        self.assertFalse((out/"site").exists())


if __name__ == "__main__":
    unittest.main()
