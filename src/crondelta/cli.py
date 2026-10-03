"""CLI and manifest loading. Compare never installs or creates environments."""

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .comparison import EXIT_CODES, compare, input_report
from .config import Case, InvalidInput, UnsupportedScope, keys
from .reports import human


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidInput(message)


def parser() -> argparse.ArgumentParser:
    root = Parser(
        prog="crondelta", description="Compare actual cron engines in a finite UTC window."
    )
    root.add_argument("--version", action="version", version=f"CronDelta {__version__}")
    sub = root.add_subparsers(dest="command", required=True)
    command = sub.add_parser("compare")
    command.add_argument("--manifest", type=Path)
    command.add_argument("--format", choices=("human", "json"), default="human")
    command.add_argument("--expression", help="shared expression; each side can override")
    for side in ("left", "right"):
        command.add_argument(f"--{side}-engine", choices=("croniter", "apscheduler"))
        command.add_argument(f"--{side}-expression")
        command.add_argument(f"--{side}-options", help="JSON object")
        command.add_argument(f"--{side}-python")
        command.add_argument(f"--{side}-version", help="exact required installed engine version")
    command.add_argument("--timezone")
    command.add_argument("--start")
    command.add_argument("--end")
    command.add_argument("--deadline-seconds", type=float)
    command.add_argument("--max-occurrences", type=int)
    command.add_argument("--stdout-bytes", type=int)
    command.add_argument("--stderr-bytes", type=int)
    command.add_argument("--context", type=int)
    return root


def load_cases(args) -> list[Case]:
    if args.manifest:
        conflicts = [
            key
            for key, value in vars(args).items()
            if key not in {"command", "manifest", "format"} and value is not None
        ]
        if conflicts:
            raise InvalidInput("manifest cannot be combined with profile/window/limit flags")
        with args.manifest.open("rb") as source:
            payload = source.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise InvalidInput("manifest exceeds 1 MiB")
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise InvalidInput("manifest must be an object")
        keys(data, {"schema_version", "cases"}, "manifest")
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise InvalidInput("manifest schema_version must be 1")
        cases = data.get("cases")
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
            raise InvalidInput("manifest needs 1..100 named cases")
        if any(not isinstance(case, dict) or "name" not in case for case in cases):
            raise InvalidInput("manifest cases must have explicit names")
        parsed = [Case.parse(case) for case in cases]
        if len({case.name for case in parsed}) != len(parsed):
            raise InvalidInput("case names must be unique")
        return parsed
    data = {key: getattr(args, key) for key in ("timezone", "start", "end")}
    for side in ("left", "right"):
        profile = {}
        for key in ("engine", "expression", "options", "python", "version"):
            value = getattr(args, f"{side}_{key}")
            if key == "expression" and value is None:
                value = args.expression
            if value is not None:
                profile[key] = json.loads(value) if key == "options" else value
        if "engine" not in profile:
            raise InvalidInput(f"--{side}-engine is required")
        data[side] = profile
    data["limits"] = {
        key: getattr(args, key)
        for key in (
            "deadline_seconds",
            "max_occurrences",
            "stdout_bytes",
            "stderr_bytes",
            "context",
        )
        if getattr(args, key) is not None
    }
    return [Case.parse(data)]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_output = "--format=json" in argv or any(
        a == "--format" and b == "json" for a, b in zip(argv, argv[1:], strict=False)
    )
    try:
        args = parser().parse_args(argv)
        json_output = args.format == "json"
        cases = load_cases(args)
        reports = [compare(case) for case in cases]
        if args.manifest:
            priority = (
                "ENGINE_ERROR",
                "INVALID_INPUT",
                "UNRESOLVED",
                "DIFFERENT",
                "MATCH_WITHIN_WINDOW",
            )
            outcome = next(item for item in priority if any(r["outcome"] == item for r in reports))
            result = {"schema_version": 1, "outcome": outcome, "cases": reports}
        else:
            result = reports[0]
    except UnsupportedScope as exc:
        result = input_report("UNRESOLVED", str(exc))
    except (InvalidInput, OSError, ValueError, UnicodeError, RecursionError) as exc:
        result = input_report("INVALID_INPUT", str(exc))
    print(json.dumps(result, indent=2, allow_nan=False) if json_output else human(result))
    return EXIT_CODES[result["outcome"]]
