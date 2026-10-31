"""Backfill imports preserve unrelated state and distinguish live, missing and failed."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("backfill", ROOT / "discovery/backfill.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


class BackfillTests(unittest.TestCase):
    def test_athena_import_checks_provider_and_preserves_existing_delivery_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, csv, out = root / "boards.json", root / "athena.csv", root / "output"
            initial = {"_meta": {"completed_crawls": ["old"]}, "existing": {"de": True, "checked": "2026-10-01"}}
            registry.write_text(json.dumps(initial))
            csv.write_text('url\nhttps://job-boards.greenhouse.io/new/jobs/1\nhttps://job-boards.greenhouse.io/new/jobs/2\nhttps://evil.test/new\nhttps://jobs.ashbyhq.com/other\n')
            with patch.object(backfill.sources, "fetch_greenhouse", return_value=[{"location": "Berlin"}]) as fetch, \
                 contextlib.redirect_stdout(io.StringIO()):
                result = backfill.main(["--athena", str(csv), "--boards", str(registry), "--add", "--out", str(out)])
            self.assertEqual(result, 0)
            fetch.assert_called_once()
            saved = json.loads(registry.read_text())
            self.assertEqual({k: saved[k] for k in initial}, initial)
            self.assertEqual(saved["greenhouse:new"], {"de": True})
            self.assertEqual(json.loads((out / "run.json").read_text())["added"], ["greenhouse:new"])

    def test_transient_validation_is_reported_and_never_added_as_an_empty_board(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            registry, seeds, out = root / "boards.json", root / "seeds.txt", root / "output"
            registry.write_text("{}")
            seeds.write_text("greenhouse:broken\n")
            with patch.object(backfill.sources, "fetch_greenhouse", side_effect=OSError("offline")), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(backfill.main(["--seeds", str(seeds), "--boards", str(registry), "--add", "--out", str(out)]), 1)
            self.assertEqual(json.loads(registry.read_text()), {})
            report = json.loads((out / "run.json").read_text())
            self.assertEqual(report["not_found"], 0)
            self.assertEqual(len(report["errors"]), 1)
            self.assertEqual(json.loads((out / "candidates.json").read_text()), ["greenhouse:broken"])


if __name__ == "__main__":
    unittest.main()
