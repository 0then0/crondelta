"""Run controlled cases using already prepared interpreters. No installation or SQL."""

import argparse
import json
from pathlib import Path

from crondelta.comparison import compare
from crondelta.config import Case


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("examples/migration.json"))
    parser.add_argument("--left-python")
    parser.add_argument("--right-python")
    parser.add_argument("--left-version", default="6.2.4")
    parser.add_argument("--right-version", default="3.11.3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    reports = []
    for item in data["cases"]:
        for side in ("left", "right"):
            python = getattr(args, f"{side}_python")
            if python:
                item[side]["python"] = python
            item[side]["version"] = getattr(args, f"{side}_version")
        reports.append(compare(Case.parse(item)))
    expected = {
        "dom_dow": "DIFFERENT",
        "numeric_weekday": "DIFFERENT",
        "named_weekday_control": "MATCH_WITHIN_WINDOW",
        "berlin_gap": "DIFFERENT",
        "berlin_fold_control": "MATCH_WITHIN_WINDOW",
        "lord_howe_gap": "DIFFERENT",
        "synthetic_and_control": "MATCH_WITHIN_WINDOW",
    }
    for result in reports:
        assert result["outcome"] == expected[result["name"]], result
        assert result["completeness"]["window_checked"], result
        print(f"{result['name']}: {result['outcome']}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"schema_version": 1, "cases": reports}, indent=2) + "\n")


if __name__ == "__main__":
    main()
