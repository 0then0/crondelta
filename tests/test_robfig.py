import hashlib
import json
import os
from importlib.resources import files
from pathlib import Path

import pytest

from crondelta.adapters import run_adapter
from crondelta.cli import main
from crondelta.config import Case


@pytest.fixture
def worker():
    path = os.environ.get("CRONDELTA_ROBFIG_WORKER")
    if not path:
        pytest.skip("set CRONDELTA_ROBFIG_WORKER to a built Go worker")
    return Path(path).absolute()


def case_data(worker, expression="* * * * *", **overrides):
    return {
        "name": "robfig",
        "left": {"engine": "apscheduler", "expression": expression},
        "right": {"engine": "robfig/cron", "expression": expression, "worker": str(worker)},
        "timezone": "UTC",
        "start": "2026-06-01T09:00:00Z",
        "end": "2026-06-01T09:02:00Z",
        **overrides,
    }


def run_manifest(data, tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": [data]}))
    code = main(["compare", "--manifest", str(path), "--format", "json"])
    envelope = json.loads(capsys.readouterr().out)
    return code, envelope["cases"][0]


@pytest.mark.parametrize(
    "expression",
    ["@every 1h", "TZ=UTC 0 9 * * MON", "CRON_TZ=UTC 0 9 * * MON", "* * TZ=UTC * *"],
)
def test_unsupported_expressions_are_rejected_before_launch(expression, tmp_path, capsys):
    data = case_data(tmp_path / "missing")
    data["right"]["expression"] = expression
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [data]}))
    assert main(["compare", "--manifest", str(manifest), "--format", "json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["termination_reason"] == "configuration"
    assert "descriptor or TZ prefix" in report["reason"]


def test_cli_reports_a_missing_explicit_worker(tmp_path, capsys):
    args = [
        "compare",
        "--format",
        "json",
        "--left-engine",
        "apscheduler",
        "--right-engine",
        "robfig/cron",
        "--expression",
        "* * * * *",
        "--timezone",
        "UTC",
        "--start",
        "2026-06-01T09:00:00Z",
        "--end",
        "2026-06-01T09:02:00Z",
        "--right-worker",
        str(tmp_path / "missing"),
    ]
    assert main(args) == 4
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 4
    assert report["termination_reason"]["right"] == "engine_error"
    assert "cannot start adapter" in report["adapter_diagnostics"]["right"]["detail"]


@pytest.mark.parametrize(
    "start,end,minutes",
    [
        ("09:00:00", "09:02:00", [0, 1]),
        ("09:00:00.000001", "09:02:00.000001", [1, 2]),
        ("09:00:00.000001", "09:01:00", []),
    ],
)
def test_real_worker_window_boundaries(worker, start, end, minutes):
    case = Case.parse(
        case_data(
            worker,
            start=f"2026-06-01T{start}Z",
            end=f"2026-06-01T{end}Z",
            limits={"max_occurrences": max(len(minutes), 1)},
        )
    )
    stream = run_adapter(case, case.right)
    assert stream.complete, stream
    assert [item["utc"] for item in stream.events] == [
        f"2026-06-01T09:{minute:02d}:00.000000Z" for minute in minutes
    ]


@pytest.mark.parametrize(
    "expression,timezone,limits,status,code",
    [
        ("* * * * *", "Mars/Olympus", {}, "invalid_timezone", 2),
        ("0 9 * * 7", "UTC", {}, "rejected", 2),
        ("0 0 31 2 *", "UTC", {}, "search_limit", 3),
        ("* * * * *", "UTC", {"max_occurrences": 1}, "occurrence_cap", 3),
    ],
)
def test_real_worker_reports_incomplete_runs(
    worker, expression, timezone, limits, status, code, tmp_path, capsys
):
    result, report = run_manifest(
        case_data(
            worker,
            expression,
            timezone=timezone,
            limits=limits,
        ),
        tmp_path,
        capsys,
    )
    assert result == code
    assert report["termination_reason"]["right"] == status
    assert not report["completeness"]["window_checked"]
    assert report["occurrence_counts"]["right"] is None
    assert report["adapter_diagnostics"]["right"]["detail"]


def test_pinned_tzif_overrides_ambient_zone_and_keeps_both_folds(worker, tmp_path, monkeypatch):
    zones = files("tzdata.zoneinfo")
    data = case_data(
        worker,
        "30 2 * * *",
        timezone="Europe/Berlin",
        start="2026-10-25T00:00:00Z",
        end="2026-10-25T02:00:00Z",
    )
    tzif = tmp_path / "Berlin.tzif"
    payload = zones.joinpath("Europe", "Berlin").read_bytes()
    tzif.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    decoy = tmp_path / "Europe" / "Berlin"
    decoy.parent.mkdir()
    decoy.write_bytes(zones.joinpath("UTC").read_bytes())
    monkeypatch.setenv("ZONEINFO", str(tmp_path))
    unpinned = Case.parse(data)
    fallback = run_adapter(unpinned, unpinned.right)
    assert fallback.complete and not fallback.events
    assert fallback.profile["timezone"]["source"] is None
    data["right"].update(tzif_path=str(tzif), tzif_sha256=digest)
    pinned = Case.parse(data)
    stream = run_adapter(pinned, pinned.right)
    assert stream.complete, stream
    assert [(event["utc"], event["offset_seconds"], event["fold"]) for event in stream.events] == [
        ("2026-10-25T00:30:00.000000Z", 7200, 0),
        ("2026-10-25T01:30:00.000000Z", 3600, 1),
    ]
    assert stream.profile["actual_version"] == "v3.0.1"
    assert stream.profile["runtime"]["version"].startswith("go")
    assert stream.profile["timezone"]["tzif_sha256"] == digest


def test_wrong_tzif_pin_never_runs(worker, tmp_path, capsys):
    path = tmp_path / "UTC.tzif"
    path.write_bytes(files("tzdata.zoneinfo").joinpath("UTC").read_bytes())
    data = case_data(worker)
    data["right"].update(tzif_path=str(path), tzif_sha256="0" * 64)
    code, report = run_manifest(data, tmp_path, capsys)
    assert code == 3
    assert report["termination_reason"]["right"] == "tzdata_mismatch"
    assert not report["completeness"]["right"]["accepted"]
    assert report["completeness"]["right"]["observed_count"] == 0
