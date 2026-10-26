#!/usr/bin/env python3
"""Snapshot the original radar's preferences and known companies, without sent history."""
import argparse
import json
from pathlib import Path
import subprocess


def git(*args, required=True):
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=required)
    return result.stdout if result.returncode == 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="FETCH_HEAD", help="already fetched original repository commit")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    commit = git("rev-parse", args.ref).strip()
    (args.out / "config.json").write_text(git("show", commit + ":config.json"), encoding="utf-8")
    source = {"repository": "betklab/ashby-job-radar", "commit": commit,
              "fork_commit": git("rev-parse", "HEAD").strip()}
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
    print("Catalog preferences source: " + commit)


if __name__ == "__main__":
    main()
