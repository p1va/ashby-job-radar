#!/usr/bin/env python3
"""Snapshot the original radar's preferences and known companies, without sent history."""
import argparse
import json
from pathlib import Path
import subprocess


def git(*args, required=True):
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=required)
    return result.stdout if result.returncode == 0 else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="FETCH_HEAD", help="already fetched original repository commit")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--include-seen", action="store_true", help="also snapshot sent history for a read-only digest preview")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    commit = git("rev-parse", args.ref).strip()
    config = json.loads(git("show", commit + ":config.json"))
    local = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
    # The fork opts into broader remote coverage; other upstream preferences stay current.
    overrides = {key: local[key] for key in ("sources", "include_remote_europe", "include_remote_global",
                                            "ignore_background_qualifiers") if key in local}
    config.update(overrides)
    (args.out / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    source = {"repository": "betklab/ashby-job-radar", "commit": commit,
              "fork_commit": git("rev-parse", "HEAD").strip(), "sources": config.get("sources", ["ashby"]),
              "preference_overrides": overrides}
    extra = []
    refs = [commit]
    # Keep discovery seeds available while their small upstream PR is still open.
    optional = git("rev-parse", "--verify", "origin/pr/5-serper", required=False)
    if optional:
        refs.append(optional.strip())
        source["extra_company_ref"] = optional.strip()
    for ref in refs:
        for name in ("companies.txt", "boards.json", "embedded-boards/boards.txt"):
            text = git("show", ref + ":" + name, required=False)
            if text is not None:
                extra.append("\n".join(k for k in json.loads(text) if not k.startswith("_"))
                             if name.endswith(".json") else text)
    (args.out / "extra-companies.txt").write_text("\n".join(extra), encoding="utf-8")
    (args.out / "source.json").write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    if args.include_seen:
        (args.out / "seen.json").write_text(git("show", commit + ":seen.json"), encoding="utf-8")
    print("Catalog preferences source: " + commit)


if __name__ == "__main__":
    main()
