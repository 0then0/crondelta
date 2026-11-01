"""Reproduce the tzdata 2026.5 -> 2026.6 comparison through the public CronDelta CLI.

`prepare` is the only step that installs anything: it creates two disposable environments in
an empty work directory from hash-pinned requirements. `run` never installs or downloads.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "examples" / "tzdata-lab"
MANIFEST = ROOT / "examples" / "tzdata-upgrade.json"
VERSIONS = ("2026.5", "2026.6")
DAYS = ("2026-11-02", "2026-11-03")

# Independent of croniter and CronDelta: stdlib only, reading the TZif bytes of each wheel.
CONTROL = r"""
import hashlib, importlib.metadata, importlib.resources, io, json, sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

result = {"tzdata_version": importlib.metadata.version("tzdata"), "zones": {}}
for key in sys.argv[2:]:
    tzif = importlib.resources.files("tzdata.zoneinfo").joinpath(*key.split("/")).read_bytes()
    zone = ZoneInfo.from_file(io.BytesIO(tzif), key=key)
    rows = []
    for day in sys.argv[1].split(","):
        year, month, date = map(int, day.split("-"))
        local = datetime(year, month, date, 9, 0, tzinfo=zone)
        utc = local.astimezone(UTC)
        assert local.replace(fold=1).utcoffset() == local.utcoffset()  # not a repeated time
        assert utc.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None)  # exists
        rows.append({
            "local": local.isoformat(timespec="microseconds"),
            "utc": utc.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        })
    result["zones"][key] = {"tzif_sha256": hashlib.sha256(tzif).hexdigest(), "occurrences": rows}
print(json.dumps(result))
"""

# Expected results. The hashes follow from the pinned wheels in examples/tzdata-lab.
EXPECTED = {
    "tzdata_havana_rules": {
        "zone": "America/Havana",
        "outcome": "DIFFERENT",
        "exit_code": 1,
        "tzif_sha256": (
            "9ace6b0aeab6c81338f55993ca632d15037773968137596477c8e3cca767366f",
            "d2ba5f8960acb66655c19dfd94de72a07f51cb8ef55f843b801409d460432578",
        ),
        "utc": (
            ["2026-11-02T14:00:00.000000Z", "2026-11-03T14:00:00.000000Z"],
            ["2026-11-02T13:00:00.000000Z", "2026-11-03T13:00:00.000000Z"],
        ),
    },
    "tzdata_berlin_control": {
        "zone": "Europe/Berlin",
        "outcome": "MATCH_WITHIN_WINDOW",
        "exit_code": 0,
        "tzif_sha256": ("a7fd9932d785d4d690900b834c3563c1810c1cf2e01711bcc0926af6c0767cb7",) * 2,
        "utc": (["2026-11-02T08:00:00.000000Z", "2026-11-03T08:00:00.000000Z"],) * 2,
    },
    "tzdata_utc_control": {
        "zone": "UTC",
        "outcome": "MATCH_WITHIN_WINDOW",
        "exit_code": 0,
        "tzif_sha256": ("fddce1e648a1732ac29afd9a16151b2973cdf082e7ec0c690f7e42be6b598b93",) * 2,
        "utc": (["2026-11-02T09:00:00.000000Z", "2026-11-03T09:00:00.000000Z"],) * 2,
    },
}


LABELS = [f"<lab>/tzdata-{version}/bin/python" for version in VERSIONS]


def interpreter(workdir: Path, version: str) -> Path:
    return workdir / f"tzdata-{version}" / "bin" / "python"


def prepare(args):
    workdir = args.workdir.resolve()
    if workdir.exists() and any(workdir.iterdir()):
        sys.exit(f"refusing to prepare in a non-empty directory: {workdir}")
    uv = shutil.which("uv")
    if uv is None:
        sys.exit("uv is required to prepare the lab environments")
    for version in VERSIONS:
        env = workdir / f"tzdata-{version}"
        subprocess.run([uv, "venv", "--python", args.python, str(env)], check=True)
        subprocess.run(
            [
                uv, "pip", "install", "--python", str(interpreter(workdir, version)),
                "--require-hashes", "--only-binary", ":all:",
                "-r", str(LAB / f"requirements-tzdata-{version}.txt"),
            ],
            check=True,
        )  # fmt: skip
    print(f"prepared {', '.join(VERSIONS)} in {workdir}")


def crondelta(*argv):
    child = subprocess.run(
        [sys.executable, "-m", "crondelta", *argv], capture_output=True, text=True, check=False
    )
    return child.returncode, child.stdout, child.stderr


def flag_arguments(case, pythons):
    argv = ["compare", "--format", "json", "--timezone", case["timezone"]]
    argv += ["--start", case["start"], "--end", case["end"]]
    argv += ["--expression", case["left"]["expression"], "--allow-different-tzdata"]
    for side, python in zip(("left", "right"), pythons, strict=True):
        profile = case[side]
        argv += [f"--{side}-engine", profile["engine"], f"--{side}-python", str(python)]
        argv += [f"--{side}-version", profile["version"]]
        argv += [f"--{side}-tzdata-version", profile["tzdata_version"]]
    return argv


def redact(value, replacements):
    text = json.dumps(value, indent=2)
    for original, label in replacements:
        text = text.replace(json.dumps(original)[1:-1], label)
    return json.loads(text)


def run(args):
    workdir = args.workdir.resolve()
    pythons = [interpreter(workdir, version) for version in VERSIONS]
    for python in pythons:
        if not python.exists():
            sys.exit(f"missing {python}; run `prepare` first (compare never installs anything)")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    assert [c["name"] for c in cases] == list(EXPECTED)

    control = {}
    zones = [EXPECTED[c["name"]]["zone"] for c in cases]
    for version, python in zip(VERSIONS, pythons, strict=True):
        output = subprocess.check_output(
            [str(python), "-I", "-c", CONTROL, ",".join(DAYS), *zones], text=True
        )
        control[version] = json.loads(output)
        assert control[version]["tzdata_version"] == version

    runtime_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for case in runtime_manifest["cases"]:
        for side, python in zip(("left", "right"), pythons, strict=True):
            case[side]["python"] = str(python)
    runtime_path = workdir / "manifest.json"
    runtime_path.write_text(json.dumps(runtime_manifest, indent=2) + "\n", encoding="utf-8")

    commands = []
    code, stdout, stderr = crondelta("compare", "--manifest", str(runtime_path), "--format", "json")
    assert not stderr, stderr
    report = json.loads(stdout)
    manifest_argv = ["compare", "--manifest", "<lab>/manifest.json", "--format", "json"]
    commands.append({"argv": manifest_argv, "exit_code": code, "expected_exit_code": 1})
    assert code == 1 and report["outcome"] == "DIFFERENT" and report["schema_version"] == 2
    single_exit_codes = {}
    for case, entry in zip(cases, report["cases"], strict=True):
        expected = EXPECTED[case["name"]]
        left, right = entry["profiles"]["left"]["resolved"], entry["profiles"]["right"]["resolved"]
        assert entry["name"] == case["name"] and entry["outcome"] == expected["outcome"], entry
        assert entry["completeness"]["window_checked"] and entry["schema_version"] == 2
        assert left["runtime"] == right["runtime"]
        assert left["actual_version"] == right["actual_version"] == "6.2.4"
        assert (left["timezone"]["tzdata_version"], right["timezone"]["tzdata_version"]) == VERSIONS
        assert (left["timezone"]["requested_tzdata_version"],
                right["timezone"]["requested_tzdata_version"]) == VERSIONS  # fmt: skip
        assert entry["timezone_data"]["different_tzdata_allowed"]
        assert entry["profile_differences"]["configuration"] == []
        assert entry["profile_differences"]["timezone_data_only"]
        for index, version in enumerate(VERSIONS):
            side = ("left", "right")[index]
            zone = control[version]["zones"][expected["zone"]]
            tz = entry["profiles"][side]["resolved"]["timezone"]
            assert tz["tzif_sha256"] == zone["tzif_sha256"] == expected["tzif_sha256"][index]
            assert entry["occurrence_counts"][side] == len(DAYS)
            utc = [event["utc"] for event in entry["context"][side]]
            assert utc == [row["utc"] for row in zone["occurrences"]] == expected["utc"][index]
            assert {event["local"] for event in entry["context"][side]} == {
                row["local"] for row in zone["occurrences"]
            }
        witness = entry["first_divergence"]
        assert (witness is not None) == (expected["outcome"] == "DIFFERENT")
        if witness:
            assert witness["kind"] == "occurrence" and witness["index"] == 0
            assert witness["left"]["utc"] == "2026-11-02T14:00:00.000000Z"
            assert witness["left"]["local"] == "2026-11-02T09:00:00.000000-05:00"
            assert witness["right"]["utc"] == "2026-11-02T13:00:00.000000Z"
            assert witness["right"]["local"] == "2026-11-02T09:00:00.000000-04:00"
        code, stdout, stderr = crondelta(*flag_arguments(case, pythons))
        assert not stderr and code == expected["exit_code"], (case["name"], code, stdout)
        assert json.loads(stdout)["outcome"] == expected["outcome"]
        single_exit_codes[case["name"]] = code
        argv = flag_arguments(case, LABELS)
        commands.append(
            {"argv": argv, "exit_code": code, "expected_exit_code": expected["exit_code"]}
        )

    # Strict default: the same Havana case without explicit permission must stay UNRESOLVED.
    strict = flag_arguments(cases[0], pythons)
    strict.remove("--allow-different-tzdata")
    code, stdout, _ = crondelta(*strict)
    assert code == 3 and json.loads(stdout)["outcome"] == "UNRESOLVED", stdout
    labelled = [a for a in flag_arguments(cases[0], LABELS) if a != "--allow-different-tzdata"]
    commands.append({"argv": labelled, "exit_code": code, "expected_exit_code": 3})

    replacements = [(str(workdir), "<lab>"), (str(ROOT), "<repository>")]
    result = redact(
        {
            "schema_version": 1,
            "lab": "tzdata-2026.5-to-2026.6",
            "redaction": "absolute paths of the disposable lab and repository replaced by labels",
            "commands": commands,
            "independent_control": control,
            "report": report,
        },
        replacements,
    )
    text = json.dumps(result, indent=2) + "\n"
    for private in (str(workdir), str(ROOT), str(Path.home())):
        assert private not in text, "unredacted local path in saved results"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    for case in cases:
        name = case["name"]
        print(f"{name}: {EXPECTED[name]['outcome']} (exit {single_exit_codes[name]})")
    print("strict default without --allow-different-tzdata: UNRESOLVED (exit 3)")
    print(f"independent stdlib TZif control agrees; results: {args.output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="install pinned disposable environments (needs network)")
    prep.add_argument("--workdir", type=Path, required=True)
    prep.add_argument("--python", default=sys.executable, help="interpreter for both environments")
    prep.set_defaults(handler=prepare)
    check = sub.add_parser("run", help="run the lab through the CronDelta CLI; installs nothing")
    check.add_argument("--workdir", type=Path, required=True)
    check.add_argument("--output", type=Path, required=True)
    check.set_defaults(handler=run)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
