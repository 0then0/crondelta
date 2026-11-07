"""Reproduce the cron-parser 5.8.1 -> 5.9.0 comparison through the public CronDelta CLI.

`prepare` is the only step that installs anything: it creates two disposable installations in an
empty work directory with `npm ci` from the lockfiles in examples/cron-parser-lab. `run` never
installs or downloads; it needs the prepared directory and a Node.js executable.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "examples" / "cron-parser-lab"
MANIFEST = ROOT / "examples" / "cron-parser-upgrade.json"
VERSIONS = ("5.8.1", "5.9.0")
LUXON = "3.7.2"

# Independent of CronDelta: a direct call of each installation's public API.
CONTROL = r"""
const path = require('path');
const [installation, expression, timezone, start, end] = process.argv.slice(1);
const library = path.join(installation, 'node_modules', 'cron-parser');
const { CronExpressionParser } = require(library);
const version = require(path.join(library, 'package.json')).version;
const luxon = require(path.join(installation, 'node_modules', 'luxon', 'package.json')).version;
const options = { currentDate: new Date(start), tz: timezone };
const iterator = CronExpressionParser.parse(expression, options);
const occurrences = [];
for (let next = iterator.next().toDate(); next < new Date(end); next = iterator.next().toDate()) {
  occurrences.push(next.toISOString());
}
console.log(JSON.stringify({ version, luxon, occurrences }));
"""

DAY = "T01:30:00.000Z"
EXPECTED = {
    "cron_parser_troll_gap": {
        "outcome": "DIFFERENT",
        "exit_code": 1,
        # 29 March has a two-hour gap in Antarctica/Troll (01:00 -> 03:00 local).
        "utc": (
            [
                "2026-03-27" + DAY,
                "2026-03-28" + DAY,
                "2026-03-29T23:30:00.000Z",
                "2026-03-30T23:30:00.000Z",
            ],
            [
                "2026-03-27" + DAY,
                "2026-03-28" + DAY,
                "2026-03-29" + DAY,
                "2026-03-29T23:30:00.000Z",
                "2026-03-30T23:30:00.000Z",
            ],
        ),
    },
    "cron_parser_troll_ordinary_control": {
        "outcome": "MATCH_WITHIN_WINDOW",
        "exit_code": 0,
        "utc": (["2026-03-23" + DAY, "2026-03-24" + DAY, "2026-03-25" + DAY, "2026-03-26" + DAY],)
        * 2,
    },
    "cron_parser_utc_control": {
        "outcome": "MATCH_WITHIN_WINDOW",
        "exit_code": 0,
        "utc": (["2026-03-27" + DAY, "2026-03-28" + DAY, "2026-03-29" + DAY, "2026-03-30" + DAY],)
        * 2,
    },
    "cron_parser_new_york_gap_control": {
        "outcome": "MATCH_WITHIN_WINDOW",
        "exit_code": 0,
        # 2026-03-08 02:30 does not exist in New York; both versions move it to 03:30 EDT.
        "utc": (
            [
                "2026-03-07T07:30:00.000Z",
                "2026-03-08T07:30:00.000Z",
                "2026-03-09T06:30:00.000Z",
                "2026-03-10T06:30:00.000Z",
            ],
        )
        * 2,
    },
}
LABEL = "<lab>/cron-parser-{}"


def installation(workdir: Path, version: str) -> Path:
    return workdir / f"cron-parser-{version}"


def prepare(args):
    workdir = args.workdir.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        sys.exit(f"refusing to prepare in a non-empty directory: {workdir}")
    npm = args.npm or shutil.which("npm")
    if npm is None:
        sys.exit("npm is required to prepare the lab installations")
    for version in VERSIONS:
        target = installation(workdir, version)
        target.mkdir(parents=True)
        for name in ("package.json", "package-lock.json"):
            shutil.copy(LAB / version / name, target / name)
        subprocess.run(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=target, check=True
        )
    print(f"prepared {', '.join(VERSIONS)} in {workdir}")


def crondelta(*argv):
    child = subprocess.run(
        [sys.executable, "-m", "crondelta", *argv], capture_output=True, text=True, check=False
    )
    return child.returncode, child.stdout, child.stderr


def flag_arguments(case, node, installations):
    argv = ["compare", "--format", "json", "--node", node, "--timezone", case["timezone"]]
    argv += ["--start", case["start"], "--end", case["end"]]
    argv += ["--expression", case["left"]["expression"]]
    for side, target in zip(("left", "right"), installations, strict=True):
        profile = case[side]
        argv += [f"--{side}-engine", profile["engine"], f"--{side}-installation", str(target)]
        argv += [f"--{side}-version", profile["version"]]
    return argv


def redact(value, replacements):
    text = json.dumps(value, indent=2)
    for original, label in replacements:
        text = text.replace(json.dumps(original)[1:-1], label)
    return json.loads(text)


def run(args):
    workdir = args.workdir.resolve()
    installations = [installation(workdir, version) for version in VERSIONS]
    for target in installations:
        if not (target / "node_modules" / "cron-parser").is_dir():
            sys.exit(f"missing {target}; run `prepare` first (compare never installs anything)")
    node = shutil.which(args.node) if args.node else shutil.which("node")
    if node is None:
        sys.exit("a Node.js executable is required: pass --node")
    node = str(Path(node).resolve())
    cases = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]
    assert [c["name"] for c in cases] == list(EXPECTED)

    control = {}
    for case in cases:
        rows = []
        for target in installations:
            output = subprocess.check_output(
                [node, "-e", CONTROL, str(target), case["left"]["expression"], case["timezone"],
                 case["start"], case["end"]],
                text=True,
            )  # fmt: skip
            rows.append(json.loads(output))
        control[case["name"]] = dict(zip(VERSIONS, rows, strict=True))

    runtime_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for case in runtime_manifest["cases"]:
        case["node"] = node
        for side, target in zip(("left", "right"), installations, strict=True):
            case[side]["installation"] = str(target)
    runtime_path = workdir / "manifest.json"
    runtime_path.write_text(json.dumps(runtime_manifest, indent=2) + "\n", encoding="utf-8")

    commands = []
    code, stdout, stderr = crondelta("compare", "--manifest", str(runtime_path), "--format", "json")
    assert not stderr, stderr
    report = json.loads(stdout)
    manifest_argv = ["compare", "--manifest", "<lab>/manifest.json", "--format", "json"]
    commands.append({"argv": manifest_argv, "exit_code": code, "expected_exit_code": 1})
    assert code == 1 and report["outcome"] == "DIFFERENT" and report["schema_version"] == 3
    exit_codes = {}
    for case, entry in zip(cases, report["cases"], strict=True):
        name, expected = case["name"], EXPECTED[case["name"]]
        left, right = entry["profiles"]["left"]["resolved"], entry["profiles"]["right"]["resolved"]
        assert entry["name"] == name and entry["outcome"] == expected["outcome"], entry
        assert entry["schema_version"] == 3 and entry["completeness"]["window_checked"]
        assert (left["actual_version"], right["actual_version"]) == VERSIONS
        assert (left["loaded"]["luxon"]["version"], right["loaded"]["luxon"]["version"]) == (
            LUXON,
        ) * 2
        assert left["runtime"] == right["runtime"]
        assert entry["timezone_data"]["status"] == "same_runtime"
        assert entry["profile_differences"]["engine_version_only"] is True
        for index, side in enumerate(("left", "right")):
            direct = control[name][VERSIONS[index]]
            assert direct["version"] == VERSIONS[index] and direct["luxon"] == LUXON
            utc = [event["utc"].replace("000Z", "Z") for event in entry["context"][side]]
            assert direct["occurrences"] == expected["utc"][index], (name, side, direct)
            assert utc == direct["occurrences"], (name, side, utc, direct)
            assert entry["occurrence_counts"][side] == len(direct["occurrences"])
        witness = entry["first_divergence"]
        assert (witness is not None) == (expected["outcome"] == "DIFFERENT")
        if witness:
            assert witness["kind"] == "occurrence" and witness["index"] == 2
            assert witness["left"]["utc"] == "2026-03-29T23:30:00.000000Z"
            assert witness["left"]["local"] == "2026-03-30T01:30:00.000000+02:00"
            assert witness["right"]["utc"] == "2026-03-29T01:30:00.000000Z"
            assert witness["right"]["local"] == "2026-03-29T03:30:00.000000+02:00"
        code, stdout, stderr = crondelta(*flag_arguments(case, node, installations))
        assert not stderr and code == expected["exit_code"], (name, code, stdout)
        assert json.loads(stdout)["outcome"] == expected["outcome"]
        exit_codes[name] = code
        argv = flag_arguments(case, "<node>", [LABEL.format(v) for v in VERSIONS])
        commands.append(
            {"argv": argv, "exit_code": code, "expected_exit_code": expected["exit_code"]}
        )

    npm = args.npm or shutil.which("npm") or "npm"
    replacements = [(str(workdir), "<lab>"), (str(ROOT), "<repository>"), (node, "<node>")]
    result = redact(
        {
            "schema_version": 1,
            "lab": "cron-parser-5.8.1-to-5.9.0",
            "redaction": "absolute paths of the lab, repository and Node.js replaced by labels",
            "preparation": [
                {
                    "cwd": LABEL.format(version),
                    "argv": [Path(npm).name, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                    "lockfile": f"examples/cron-parser-lab/{version}/package-lock.json",
                }
                for version in VERSIONS
            ],
            "commands": commands,
            "independent_control": control,
            "report": report,
        },
        replacements,
    )
    text = json.dumps(result, indent=2) + "\n"
    for private in (str(workdir), str(ROOT), str(Path.home()), node):
        assert private not in text, "unredacted local path in saved results"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    for name in EXPECTED:
        print(f"{name}: {EXPECTED[name]['outcome']} (exit {exit_codes[name]})")
    print(f"direct public API calls agree for both installations; results: {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="install pinned disposable installations (needs network)")
    prep.add_argument("--workdir", type=Path, required=True)
    prep.add_argument("--npm", help="npm executable (default: from PATH)")
    prep.set_defaults(handler=prepare)
    check = sub.add_parser("run", help="run the lab through the CronDelta CLI; installs nothing")
    check.add_argument("--workdir", type=Path, required=True)
    check.add_argument("--node", help="Node.js executable used for both sides (default: PATH)")
    check.add_argument("--npm", help="recorded in the results only")
    check.add_argument("--output", type=Path, required=True)
    check.set_defaults(handler=run)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
