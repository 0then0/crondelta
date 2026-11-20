"""Prepare hashed TZif fixtures and verify a CronDelta report against both public APIs."""

import argparse
import hashlib
import io
import json
import subprocess
from datetime import datetime, timezone
from importlib import metadata, resources
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

MANIFEST = Path(__file__).resolve().parent.parent / "robfig-cron.json"
COMMANDS = [
    "lab=$(mktemp -d)",
    'uv run --no-sync python examples/robfig-cron-lab/check.py prepare --workdir "$lab"',
    '(cd workers/robfig-cron && go build -mod=readonly -o "$lab/robfig-worker" .)',
    '(cd workers/robfig-cron && go build -mod=readonly -o "$lab/robfig-direct" '
    "../../examples/robfig-cron-lab/direct.go)",
    'uv run --no-sync crondelta compare --manifest "$lab/manifest.json" --format json '
    '> "$lab/report.json"  # exit 1',
    'uv run --no-sync python examples/robfig-cron-lab/check.py verify --workdir "$lab" '
    '"$lab/report.json"',
]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def prepare(workdir):
    manifest = read_json(MANIFEST)
    workdir.mkdir(parents=True, exist_ok=True)
    for case in manifest["cases"]:
        left, right = case["left"], case["right"]
        key = case["timezone"]
        assert metadata.version("tzdata") == left["tzdata_version"]
        tzif = resources.files("tzdata.zoneinfo").joinpath(*key.split("/")).read_bytes()
        assert hashlib.sha256(tzif).hexdigest() == right["tzif_sha256"], key
        path = workdir / Path(right["tzif_path"]).name
        path.write_bytes(tzif)
        right["tzif_path"] = str(path)
        right["worker"] = str(workdir / Path(right["worker"]).name)
    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def utc_text(instant):
    return instant.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_instant(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def pinned_zone(case):
    tzif = Path(case["right"]["tzif_path"]).read_bytes()
    assert hashlib.sha256(tzif).hexdigest() == case["right"]["tzif_sha256"], case["name"]
    return ZoneInfo.from_file(io.BytesIO(tzif), key=case["timezone"])


def direct_apscheduler(case, zone):
    trigger = CronTrigger.from_crontab(case["left"]["expression"], timezone=zone)
    start, end = parse_instant(case["start"]), parse_instant(case["end"])
    dates = []
    previous = None
    for _ in range(10):
        fired = trigger.get_next_fire_time(previous, previous or start.astimezone(zone))
        if fired is None or fired.astimezone(timezone.utc) >= end:
            return dates
        dates.append(utc_text(fired))
        previous = fired
    raise ValueError(f"{case['name']}: direct APScheduler result did not reach the end")


def verify(workdir, report_path):
    manifest_path = workdir / "manifest.json"
    manifest = read_json(manifest_path)
    report = read_json(report_path)
    assert report["outcome"] == "DIFFERENT"
    direct_robfig = json.loads(
        subprocess.check_output([str(workdir / "robfig-direct")], input=manifest_path.read_bytes())
    )
    results = []
    for case, entry in zip(manifest["cases"], report["cases"], strict=True):
        name = case["name"]
        assert entry["name"] == name and entry["completeness"]["window_checked"], entry
        assert entry["timezone_data"]["tzif_equal"] is True, name
        assert metadata.version("APScheduler") == case["left"]["version"]
        zone = pinned_zone(case)
        apscheduler_dates = direct_apscheduler(case, zone)
        robfig_dates = direct_robfig[name]
        assert entry["outcome"] == (
            "MATCH_WITHIN_WINDOW" if apscheduler_dates == robfig_dates else "DIFFERENT"
        ), name
        digest = case["right"]["tzif_sha256"]
        for side, dates in (("left", apscheduler_dates), ("right", robfig_dates)):
            profile = entry["profiles"][side]["resolved"]
            assert profile["actual_version"] == case[side]["version"], (name, side)
            assert profile["timezone"]["tzif_sha256"] == digest, (name, side)
            assert profile["timezone"]["key"] == case["timezone"], (name, side)
            assert entry["occurrence_counts"][side] == len(dates), (name, side)
            context = entry["context"][side]
            assert [parse_instant(event["utc"]) for event in context] == [
                parse_instant(date) for date in dates
            ], (
                name,
                side,
            )
            for event in context:
                local = parse_instant(event["utc"]).astimezone(zone)
                assert event["roundtrip_local"] == local.isoformat(timespec="microseconds")
                assert event["roundtrip_fold"] == local.fold
        results.append(
            {
                "name": name,
                "expression": case["left"]["expression"],
                "timezone": case["timezone"],
                "start": case["start"],
                "end": case["end"],
                "tzif_sha256": digest,
                "outcome": entry["outcome"],
                "window_checked": entry["completeness"]["window_checked"],
                "apscheduler_utc": apscheduler_dates,
                "robfig_cron_utc": robfig_dates,
            }
        )
    profiles = report["cases"][0]["profiles"]
    left, right = profiles["left"]["resolved"], profiles["right"]["resolved"]
    evidence = {
        "report_schema_version": report["schema_version"],
        "commands": COMMANDS,
        "comparison_exit_code": 1,
        "outcome": report["outcome"],
        "versions": {
            "APScheduler": left["actual_version"],
            "Python": left["runtime"]["version"],
            "tzdata": left["timezone"]["tzdata_version"],
            "robfig/cron": right["actual_version"],
            "Go": right["runtime"]["version"],
        },
        "robfig_cron_module_sum": right["loaded"]["github.com/robfig/cron/v3"]["sum"],
        "tzif_sources": {
            "APScheduler": left["timezone"]["source"],
            "robfig/cron": right["timezone"]["source"],
        },
        "direct_library_checks": "Both public APIs reproduced the complete UTC streams.",
        "cases": results,
    }
    print(json.dumps(evidence, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("report", type=Path, nargs="?")
    args = parser.parse_intermixed_args()
    if args.action == "prepare":
        if args.report is not None:
            parser.error("prepare does not take a report")
        prepare(args.workdir.resolve())
    else:
        if args.report is None:
            parser.error("verify requires a JSON report")
        verify(args.workdir.resolve(), args.report)


if __name__ == "__main__":
    main()
