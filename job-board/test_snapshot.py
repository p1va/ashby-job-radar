"""Fork opt-ins reach each collector without replacing upstream role preferences."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import snapshot


class SnapshotTests(unittest.TestCase):
    def test_only_explicit_fork_overrides_replace_upstream_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "job-board").mkdir()
            local = {"sources": ["ashby", "greenhouse"], "include_remote_europe": True,
                     "include_remote_global": True, "ignore_background_qualifiers": True,
                     "title_include": ["do not override upstream"]}
            (root / "config.json").write_text(json.dumps(local))
            upstream = {"sources": ["ashby"], "include_remote_europe": False, "title_include": ["product"],
                        "focus_cities": ["Berlin", "Munich"]}
            def git(*args, **kwargs):
                if args == ("rev-parse", "--verify", "origin/pr/5-serper"):
                    return None
                if args[0] == "rev-parse":
                    return "commit123\n"
                if args[-1].endswith(":config.json"):
                    return json.dumps(upstream)
                if args[-1].endswith(":boards.json"):
                    return '{}'
                if args[-1].endswith(":seen.json"):
                    return '{"seen-id":"2026-10-31"}'
                return "example\n"
            out = root / "inputs"
            with patch.object(snapshot, "__file__", str(root / "job-board/snapshot.py")), patch.object(snapshot, "git", side_effect=git):
                snapshot.main(["--include-seen", "--out", str(out)])
            cfg = json.loads((out / "config.json").read_text())
            self.assertEqual(cfg["title_include"], ["product"])
            self.assertEqual(cfg["focus_cities"], ["Berlin", "Munich"])
            for key in ["include_remote_europe", "include_remote_global", "ignore_background_qualifiers"]:
                self.assertIs(cfg[key], True)
            self.assertEqual(json.loads((out / "source.json").read_text())["preference_overrides"]["sources"], local["sources"])
            self.assertEqual(json.loads((out / "seen.json").read_text()), {"seen-id": "2026-10-31"})


if __name__ == "__main__":
    unittest.main()
