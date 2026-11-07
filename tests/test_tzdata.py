"""Explicit comparison of schedules across timezone database versions.

Synthetic databases (see conftest) keep these tests independent of any installed tzdata and
of the network; real pinned releases are covered by the lab and optional integration tests.
"""

import hashlib
import importlib.resources
import io
import json
import os
import subprocess
import time
from dataclasses import replace
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo, reset_tzpath

import pytest
from conftest import DATABASES, event, tzif
from test_transport import command, header, record

from crondelta import _worker, adapters
from crondelta.adapters import run_adapter
from crondelta.cli import main
from crondelta.comparison import compare, compare_streams, profile_differences
from crondelta.config import Case, InvalidInput
from crondelta.reports import human

UTC = dt_timezone.utc

OLD, NEW, REISSUED, PARTIAL = "2100.1", "2100.2", "2100.3", "2100.4"
WINDOW = {"start": "2026-11-02T00:00:00Z", "end": "2026-11-04T00:00:00Z"}


@pytest.fixture
def tz_case(make_case, tz_env):
    def make(left, right, *, timezone="America/Havana", allow=True, pins=(None, None), **kwargs):
        kwargs = WINDOW | kwargs
        case = make_case(
            "0 9 * * *",
            left_engine="croniter",
            right_engine="croniter",
            timezone=timezone,
            **kwargs,
        )
        return replace(
            case,
            left=replace(case.left, python=tz_env(left), tzdata_version=pins[0]),
            right=replace(case.right, python=tz_env(right), tzdata_version=pins[1]),
            allow_different_tzdata=allow,
        )

    return make


def utcs(report, side):
    return [e["utc"][:16] for e in report["context"][side]]


def test_default_remains_strict_and_explains_how_to_allow(tz_case):
    result = compare(tz_case(OLD, NEW, allow=False))
    assert result["outcome"] == "UNRESOLVED"
    assert result["schema_version"] == 1
    assert "different timezone data" in result["reason"]
    assert (
        "--allow-different-tzdata" in result["reason"]
        and "allow_different_tzdata" in result["reason"]
    )
    assert result["timezone_data"]["status"] == "different"
    assert not result["timezone_data"]["different_tzdata_allowed"]
    assert result["first_divergence"] is None
    assert result["completeness"]["window_checked"]  # both streams were still enumerated
    text = human(result)
    assert "Timezone data: different, comparison not allowed" in text
    assert "cannot be attributed" not in text  # no occurrence comparison took place


def test_strict_mode_also_rejects_equal_bytes_from_other_package_version(tz_case):
    result = compare(tz_case(OLD, NEW, timezone="Europe/Berlin", allow=False))
    assert result["outcome"] == "UNRESOLVED"
    assert result["timezone_data"]["tzif_equal"] and not result["timezone_data"]["versions_equal"]


def test_strict_mode_with_identical_data_still_matches(tz_case):
    result = compare(tz_case(OLD, OLD, allow=False))
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert result["schema_version"] == 1
    assert result["timezone_data"]["status"] == "identical"
    assert result["profile_differences"] == {
        "configuration": [],
        "provenance": [],
        "timezone_data_only": False,
    }


def test_different_tzdata_yields_real_occurrence_difference(tz_case):
    result = compare(tz_case(OLD, NEW))
    assert result["outcome"] == "DIFFERENT"
    assert result["schema_version"] == 2
    assert result["completeness"]["window_checked"]
    assert result["occurrence_counts"] == {"left": 2, "right": 2}
    witness = result["first_divergence"]
    assert witness["kind"] == "occurrence" and witness["index"] == 0
    assert witness["left"]["utc"] == "2026-11-02T14:00:00.000000Z"
    assert witness["left"]["local"] == "2026-11-02T09:00:00.000000-05:00"
    assert witness["right"]["utc"] == "2026-11-02T13:00:00.000000Z"
    assert witness["right"]["local"] == "2026-11-02T09:00:00.000000-04:00"
    assert utcs(result, "right") == ["2026-11-02T13:00", "2026-11-03T13:00"]
    assert result["timezone_data"] == {
        "different_tzdata_allowed": True,
        "status": "different",
        "versions_equal": False,
        "tzif_equal": False,
    }
    left, right = (result["timezone_provenance"][side] for side in ("left", "right"))
    assert (left["tzdata_version"], right["tzdata_version"]) == (OLD, NEW)
    assert left["tzif_sha256"] == hashlib.sha256(DATABASES[OLD]["America/Havana"]).hexdigest()
    assert right["tzif_sha256"] == hashlib.sha256(DATABASES[NEW]["America/Havana"]).hexdigest()
    assert left["source"] == right["source"] == "tzdata.zoneinfo"


def test_same_engine_version_with_different_tzdata_isolates_timezone_data(tz_case):
    result = compare(tz_case(OLD, NEW))
    versions = {result["profiles"][s]["resolved"]["actual_version"] for s in ("left", "right")}
    assert len(versions) == 1
    assert result["profile_differences"] == {
        "configuration": [],
        "provenance": ["tzdata_version", "tzif_sha256"],
        "timezone_data_only": True,
    }
    text = human(result)
    assert "Timezone data: different, comparison explicitly allowed" in text
    assert "equal; other dependencies are not compared" in text
    assert "defect" not in text and "buggy" not in text


@pytest.mark.parametrize("timezone", ["Europe/Berlin", "UTC"])
def test_equal_tzif_bytes_from_different_package_versions_match(tz_case, timezone):
    result = compare(tz_case(OLD, NEW, timezone=timezone))
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert result["completeness"]["window_checked"]
    assert result["timezone_data"]["status"] == "different"
    assert result["timezone_data"]["tzif_equal"] and not result["timezone_data"]["versions_equal"]
    assert result["profile_differences"]["provenance"] == ["tzdata_version"]
    assert result["first_divergence"] is None
    text = human(result)
    assert "Equal UTC streams despite the different timezone data" in text
    assert "not global equivalence" in text


def test_different_tzif_with_equal_occurrences_is_not_a_difference(tz_case):
    result = compare(tz_case(OLD, REISSUED))
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert result["timezone_data"]["tzif_equal"] is False
    assert result["profile_differences"]["provenance"] == ["tzdata_version", "tzif_sha256"]
    assert utcs(result, "left") == utcs(result, "right") == ["2026-11-02T14:00", "2026-11-03T14:00"]


def test_allowing_identical_data_does_not_change_the_comparison(tz_case):
    result = compare(tz_case(OLD, OLD, allow=True))
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert result["timezone_data"]["status"] == "identical"


@pytest.mark.parametrize(
    "start,end,outcome,counts",
    [
        # Start is inclusive: only the old database has an event exactly at start; the new
        # database's 13:00Z event precedes it. End is exclusive: 13:00Z on Nov 3 is excluded.
        ("2026-11-02T14:00:00Z", "2026-11-03T13:00:00Z", "DIFFERENT", {"left": 1, "right": 0}),
        (
            "2026-11-02T14:00:00.000001Z",
            "2026-11-03T13:00:00Z",
            "MATCH_WITHIN_WINDOW",
            {"left": 0, "right": 0},
        ),
        (
            "2026-11-02T13:00:00Z",
            "2026-11-02T13:00:00.000001Z",
            "DIFFERENT",
            {"left": 0, "right": 1},
        ),
        ("2026-11-02T13:00:00Z", "2026-11-02T13:00:00Z", None, None),
    ],
)
def test_window_boundaries_are_start_inclusive_end_exclusive(tz_case, start, end, outcome, counts):
    if outcome is None:
        with pytest.raises(InvalidInput):
            tz_case(OLD, NEW, start=start, end=end)
        return
    result = compare(tz_case(OLD, NEW, start=start, end=end))
    assert result["outcome"] == outcome
    assert result["occurrence_counts"] == counts
    assert result["completeness"]["window_checked"]
    if outcome == "DIFFERENT" and counts["right"] == 0:
        assert result["first_divergence"]["right"] is None  # proven absence, window complete
    assert result["empty_window"] == (counts == {"left": 0, "right": 0})


def test_requested_equal_to_resolved_is_recorded(tz_case):
    result = compare(tz_case(OLD, NEW, pins=(OLD, NEW)))
    assert result["outcome"] == "DIFFERENT"
    for side, version in (("left", OLD), ("right", NEW)):
        tz = result["timezone_provenance"][side]
        assert tz["requested_tzdata_version"] == tz["tzdata_version"] == version
        assert result["profiles"][side]["requested"]["tzdata_version"] == version
    assert "(requested: 2100.1)" in human(result)


@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize("side", ["left", "right"])
def test_requested_tzdata_mismatch_never_runs_under_another_database(tz_case, side, allow):
    pins = ("2100.9", None) if side == "left" else (None, "2100.9")
    result = compare(tz_case(OLD, OLD, pins=pins, allow=allow))
    assert result["outcome"] == "UNRESOLVED"
    assert result["termination_reason"][side] == "tzdata_mismatch"
    assert result["completeness"][side]["observed_count"] == 0
    assert not result["completeness"]["window_checked"]
    assert result["first_divergence"] is None
    assert result["occurrence_counts"][side] is None
    assert "requested tzdata 2100.9, installed 2100.1" in result["reason"]
    tz = result["timezone_provenance"][side]
    assert (tz["requested_tzdata_version"], tz["tzdata_version"]) == ("2100.9", OLD)


def test_engine_version_mismatch_takes_precedence_and_is_consistent(tz_case):
    case = tz_case(OLD, OLD, pins=("2100.9", None))
    case = replace(case, left=replace(case.left, version="0.0.0"))
    result = compare(case)
    assert result["outcome"] == "UNRESOLVED"
    assert result["termination_reason"]["left"] == "version_mismatch"
    assert "requested engine version is not installed" in result["reason"]


def test_missing_zone_in_one_database_is_explicit(tz_case):
    result = compare(tz_case(OLD, PARTIAL))
    assert result["outcome"] == "UNRESOLVED"
    assert result["termination_reason"] == {"left": "complete", "right": "invalid_timezone"}
    assert "right:" in result["reason"] and f"tzdata {PARTIAL}" in result["reason"]
    assert result["completeness"]["right"]["observed_count"] == 0
    assert result["first_divergence"] is None  # nothing from the left zone stands in for it
    assert result["timezone_provenance"]["right"] is None
    assert result["timezone_data"]["status"] == "unresolved"
    assert result["profile_differences"]["provenance"] is None
    assert result["profile_differences"]["timezone_data_only"] is None


@pytest.mark.parametrize("left,right,side", [(PARTIAL, OLD, "left"), (OLD, PARTIAL, "right")])
def test_missing_zone_without_permission_stays_invalid_input(tz_case, left, right, side):
    result = compare(tz_case(left, right, allow=False))
    assert result["outcome"] == "INVALID_INPUT"
    assert f"{side}:" in result["reason"]


@pytest.mark.parametrize("allow", [False, True])
def test_zone_missing_everywhere_is_invalid_input(tz_case, allow):
    result = compare(tz_case(OLD, PARTIAL, timezone="Missing/Zone", allow=allow))
    assert result["outcome"] == "INVALID_INPUT"
    assert "left:" in result["reason"] and "right:" in result["reason"]


def test_missing_tzdata_distribution_is_an_engine_error(tz_case):
    for pins in ((None, None), (OLD, None)):
        result = compare(tz_case(None, OLD, pins=pins))
        assert result["outcome"] == "ENGINE_ERROR"
        assert result["termination_reason"]["left"] == "missing_dependency"
        assert result["timezone_provenance"]["left"] is None


def test_difference_proven_in_capped_prefixes_across_tzdata(tz_case):
    result = compare(tz_case(OLD, NEW, limits={"max_occurrences": 1}))
    assert result["outcome"] == "DIFFERENT"
    assert not result["completeness"]["window_checked"]
    assert result["first_divergence"]["index"] == 0


def test_occurrence_cap_prevents_false_match_across_tzdata(tz_case):
    result = compare(tz_case(OLD, NEW, timezone="Europe/Berlin", limits={"max_occurrences": 1}))
    assert result["outcome"] == "UNRESOLVED"
    assert result["termination_reason"] == {"left": "occurrence_cap", "right": "occurrence_cap"}
    assert not result["completeness"]["window_checked"]
    assert result["occurrence_counts"] == {"left": None, "right": None}


def test_exact_cap_on_both_sides_still_completes(tz_case):
    result = compare(tz_case(OLD, NEW, timezone="Europe/Berlin", limits={"max_occurrences": 2}))
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"


def test_deadline_prevents_false_match_across_tzdata(tz_case):
    case = tz_case(OLD, NEW, timezone="Europe/Berlin", limits={"deadline_seconds": 0.01})
    result = compare(case)
    assert result["outcome"] == "UNRESOLVED"
    assert result["termination_reason"] == {"left": "timeout", "right": "timeout"}
    assert not result["completeness"]["window_checked"]


def test_timed_out_prefix_cannot_match_across_tzdata(make_case):
    case = make_case(limits={"deadline_seconds": 0.3})
    messages = [record("accepted"), record("occurrence", occurrence=event(0))]
    left = run_adapter(
        case, case.left, command=command([header(case, "2100.1"), *messages], "time.sleep(60)")
    )
    right = run_adapter(
        case,
        case.left,
        command=command(
            [header(case, "2100.2", "b" * 64), *messages, record("end", status="complete")]
        ),
    )
    assert left.status == "timeout" and right.complete
    assert compare_streams(left, right, allow_different_tzdata=True)[0] == "UNRESOLVED"
    # An incomplete shorter prefix cannot prove the absence of the longer stream's next event.
    longer = run_adapter(
        case,
        case.left,
        command=command(
            [
                header(case, "2100.2", "b" * 64),
                *messages,
                record("occurrence", occurrence=event(1)),
                record("end", status="complete"),
            ]
        ),
    )
    assert compare_streams(left, longer, allow_different_tzdata=True)[0] == "UNRESOLVED"


def test_several_changed_factors_are_listed_without_causal_claim(tz_case):
    case = tz_case(OLD, NEW)
    case = replace(
        case,
        right=replace(case.right, engine="apscheduler", expression="0 10 * * *", options={}),
    )
    result = compare(case)
    differences = result["profile_differences"]
    assert differences["configuration"] == ["engine", "expression", "options"]
    assert {"engine_version", "tzdata_version", "tzif_sha256"} <= set(differences["provenance"])
    assert differences["timezone_data_only"] is False
    text = human(result)
    assert "cannot be attributed to timezone data alone" in text
    assert "equal; other dependencies" not in text


def stream_with(profile_changes):
    from crondelta.adapters import Stream

    base = {
        "actual_version": "6.2.4",
        "runtime": {"implementation": "CPython", "version": "3.13.1"},
        "timezone": {"tzdata_version": "2026.5", "tzif_sha256": "a" * 64},
    }
    profile = {**base, **{k: v for k, v in profile_changes.items() if k != "timezone"}}
    profile["timezone"] = {**base["timezone"], **profile_changes.get("timezone", {})}
    return Stream(profile=profile, accepted=True, status="complete")


@pytest.mark.parametrize(
    "changes,provenance",
    [
        ({}, []),
        ({"actual_version": "6.0.0"}, ["engine_version"]),
        ({"runtime": {"implementation": "CPython", "version": "3.14.0"}}, ["runtime"]),
        ({"timezone": {"tzdata_version": "2026.6"}}, ["tzdata_version"]),
        (
            {"timezone": {"tzdata_version": "2026.6", "tzif_sha256": "b" * 64}},
            ["tzdata_version", "tzif_sha256"],
        ),
        (
            {"actual_version": "6.0.0", "timezone": {"tzif_sha256": "b" * 64}},
            ["engine_version", "tzif_sha256"],
        ),
    ],
)
def test_profile_difference_classification(tz_case, changes, provenance):
    case = tz_case(OLD, OLD)
    result = profile_differences(case, stream_with({}), stream_with(changes))
    assert result["configuration"] == []
    assert result["provenance"] == provenance
    assert result["timezone_data_only"] == (
        bool(provenance) and set(provenance) <= {"tzdata_version", "tzif_sha256"}
    )


def test_configuration_difference_alone_is_never_timezone_only(tz_case):
    case = tz_case(OLD, OLD)
    case = replace(case, right=replace(case.right, expression="0 10 * * *"))
    result = profile_differences(
        case, stream_with({}), stream_with({"timezone": {"tzdata_version": "x"}})
    )
    assert result["configuration"] == ["expression"]
    assert result["timezone_data_only"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"tzdata_version": "2100.9"},
        {"requested_tzdata_version": "2100.9"},
        {"source": "/usr/share/zoneinfo"},
        {"zoneinfo_tzpath": ["/usr/share/zoneinfo"]},
        {"tzif_sha256": "A" * 64},
        {"tzdata_version": ""},
    ],
)
def test_worker_provenance_not_matching_transport_expectations_is_rejected(make_case, changes):
    case = make_case()
    case = replace(case, left=replace(case.left, tzdata_version="2100.1"))
    timezone = {"tzdata_version": "2100.1", "requested_tzdata_version": "2100.1"} | changes
    messages = [header(case, **timezone), record("accepted"), record("end", status="complete")]
    stream = run_adapter(case, case.left, command=command(messages))
    assert stream.status == "protocol_error"
    assert compare_streams(stream, stream)[0] == "ENGINE_ERROR"


def test_worker_that_ignores_requested_tzdata_is_rejected(make_case):
    case = make_case()
    case = replace(case, left=replace(case.left, tzdata_version="2100.1"))
    ok = header(case, "2100.2", requested_tzdata_version="2100.1")
    for tail in (
        [record("accepted"), record("end", status="complete")],  # proceeds under 2100.2
        [record("end", status="complete")],
        [record("end", status="rejected")],
    ):
        stream = run_adapter(case, case.left, command=command([ok, *tail]))
        assert stream.status == "protocol_error", tail


def test_unfounded_or_missing_tzdata_mismatch_status_is_rejected(make_case):
    case = make_case()
    pinned = replace(case.left, tzdata_version="2100.1")
    case = replace(case, left=pinned)
    claims = header(case, "2100.1", requested_tzdata_version="2100.1")
    stream = run_adapter(
        case, pinned, command=command([claims, record("end", status="tzdata_mismatch")])
    )
    assert stream.status == "protocol_error"
    unpinned = make_case()
    claims = header(unpinned, "2100.1")
    stream = run_adapter(
        unpinned, unpinned.left, command=command([claims, record("end", status="tzdata_mismatch")])
    )
    assert stream.status == "protocol_error"
    honest = header(case, "2100.2", requested_tzdata_version="2100.1")
    stream = run_adapter(
        case, pinned, command=command([honest, record("end", status="tzdata_mismatch")])
    )
    assert stream.status == "tzdata_mismatch"
    assert compare_streams(stream, stream)[0] == "UNRESOLVED"


# --- isolation from the host -------------------------------------------------------------


def run_in_process(request, capsys):
    _worker.run(request)
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


@pytest.fixture
def clean_zoneinfo():
    yield
    reset_tzpath()
    ZoneInfo.clear_cache()


def test_worker_ignores_tzpath_and_zoneinfo_cache(make_case, capsys, tmp_path, clean_zoneinfo):
    key = "America/Havana"
    poisoned = tmp_path / "system-tz"
    (poisoned / "America").mkdir(parents=True)
    (poisoned / key).write_bytes(tzif(3, "BAD"))
    reset_tzpath([str(poisoned)])
    ZoneInfo.clear_cache()
    assert ZoneInfo(key).utcoffset(datetime(2026, 11, 2)).total_seconds() == 3 * 3600  # primed
    case = make_case(timezone=key, left_engine="croniter", **WINDOW)
    records = run_in_process(case.request(case.left), capsys)
    wheel = importlib.resources.files("tzdata.zoneinfo").joinpath("America", "Havana").read_bytes()
    expected_zone = ZoneInfo.from_file(io.BytesIO(wheel), key=key)
    expected = [
        datetime(2026, 11, d, 9, tzinfo=expected_zone).astimezone(UTC).isoformat() for d in (2, 3)
    ]
    profile = next(r for r in records if r["type"] == "profile")["profile"]
    assert profile["timezone"]["tzif_sha256"] == hashlib.sha256(wheel).hexdigest()
    assert profile["timezone"]["zoneinfo_tzpath"] == []
    occurrences = [r["occurrence"]["utc"] for r in records if r["type"] == "occurrence"]
    assert [o[:19] for o in occurrences] == [e[:19] for e in expected]
    assert not any(o.startswith("2026-11-02T06") for o in occurrences)  # not the poisoned +03:00


def test_subprocess_results_ignore_host_timezone_and_tzpath(tz_case, monkeypatch, tmp_path):
    case = tz_case(OLD, NEW)
    baseline = compare(case)
    poisoned = tmp_path / "system-tz"
    (poisoned / "America").mkdir(parents=True)
    (poisoned / "Europe").mkdir()
    for key in ("America/Havana", "UTC", "Europe/Berlin"):
        (poisoned / key).write_bytes(tzif(9, "BAD"))
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    monkeypatch.setenv("PYTHONTZPATH", str(poisoned))
    monkeypatch.setenv("TZDIR", str(poisoned))
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        poisoned_run = compare(case)
    finally:
        monkeypatch.undo()
        if hasattr(time, "tzset"):
            time.tzset()

    def stable(report):
        return (
            report["outcome"],
            report["first_divergence"],
            report["context"],
            report["timezone_provenance"],
        )

    assert stable(poisoned_run) == stable(baseline)
    assert poisoned_run["outcome"] == "DIFFERENT"


def test_compare_only_starts_workers_and_never_installs(tz_case, monkeypatch):
    started = []
    real_popen = subprocess.Popen

    def recording(command, *args, **kwargs):
        started.append(command)
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(adapters.subprocess, "Popen", recording)
    case = tz_case(OLD, NEW, pins=("2100.9", NEW))
    result = compare(case)
    assert result["outcome"] == "UNRESOLVED"
    assert len(started) == 2
    for argv in started:
        assert argv[1:2] == ["-I"] and Path(argv[2]).name == "_worker.py" and len(argv) == 3


# --- CLI and manifests -------------------------------------------------------------------


@pytest.fixture
def cli(tz_env, capsys):
    def run(*extra, timezone="America/Havana", left=OLD, right=NEW, **window):
        window = WINDOW | window
        argv = [
            "compare", "--left-engine", "croniter", "--right-engine", "croniter",
            "--left-python", tz_env(left), "--right-python", tz_env(right),
            "--expression", "0 9 * * *", "--timezone", timezone,
            "--start", window["start"], "--end", window["end"], "--format", "json",
            *extra,
        ]  # fmt: skip
        code = main(argv)
        return code, json.loads(capsys.readouterr().out)

    return run


def test_cli_default_is_unresolved_exit_3(cli):
    code, report = cli()
    assert (code, report["outcome"], report["schema_version"]) == (3, "UNRESOLVED", 1)


@pytest.mark.parametrize(
    "timezone,code,outcome",
    [
        ("America/Havana", 1, "DIFFERENT"),
        ("Europe/Berlin", 0, "MATCH_WITHIN_WINDOW"),
        ("UTC", 0, "MATCH_WITHIN_WINDOW"),
    ],
)
def test_cli_allow_flag_and_pins(cli, timezone, code, outcome):
    result_code, report = cli(
        "--allow-different-tzdata", "--left-tzdata-version", OLD, "--right-tzdata-version", NEW,
        timezone=timezone,
    )  # fmt: skip
    assert (result_code, report["outcome"], report["schema_version"]) == (code, outcome, 2)
    assert report["profiles"]["left"]["requested"]["tzdata_version"] == OLD


def test_cli_pin_mismatch_exit_3(cli):
    code, report = cli("--allow-different-tzdata", "--right-tzdata-version", "2100.9")
    assert (code, report["termination_reason"]["right"]) == (3, "tzdata_mismatch")


def test_cli_missing_zone_exit_codes(cli):
    assert cli("--allow-different-tzdata", right=PARTIAL)[0] == 3
    assert cli(right=PARTIAL)[0] == 2


def test_cli_help_documents_new_flags(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["compare", "--help"])
    assert exit_info.value.code == 0
    text = capsys.readouterr().out
    for flag in ("--allow-different-tzdata", "--left-tzdata-version", "--right-tzdata-version"):
        assert flag in text


def manifest_for(tz_env, tmp_path, **case_changes):
    case = {
        "name": "havana",
        "left": {"engine": "croniter", "expression": "0 9 * * *", "python": tz_env(OLD)},
        "right": {"engine": "croniter", "expression": "0 9 * * *", "python": tz_env(NEW)},
        "timezone": "America/Havana",
        **WINDOW,
    } | case_changes
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": [case]}))
    return ["compare", "--manifest", str(path), "--format", "json"]


def test_manifest_keys_and_envelope_versions(tz_env, tmp_path, capsys):
    assert main(manifest_for(tz_env, tmp_path)) == 3
    legacy = json.loads(capsys.readouterr().out)
    assert legacy["schema_version"] == 1 and legacy["cases"][0]["schema_version"] == 1
    args = manifest_for(
        tz_env,
        tmp_path,
        allow_different_tzdata=True,
        left={
            "engine": "croniter",
            "expression": "0 9 * * *",
            "python": tz_env(OLD),
            "tzdata_version": OLD,
        },
    )
    assert main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 2 and result["cases"][0]["schema_version"] == 2
    assert result["cases"][0]["profiles"]["left"]["requested"]["tzdata_version"] == OLD


@pytest.mark.parametrize("value", ["yes", 1, None, []])
def test_manifest_allow_value_must_be_boolean(tz_env, tmp_path, capsys, value):
    assert main(manifest_for(tz_env, tmp_path, allow_different_tzdata=value)) == 2
    assert "boolean" in json.loads(capsys.readouterr().out)["reason"]


def test_manifest_rejects_flag_combination_and_bad_pin(tz_env, tmp_path, capsys):
    assert main([*manifest_for(tz_env, tmp_path), "--allow-different-tzdata"]) == 2
    assert "cannot be combined" in json.loads(capsys.readouterr().out)["reason"]
    left = {"engine": "croniter", "expression": "0 9 * * *", "tzdata_version": ""}
    assert main(manifest_for(tz_env, tmp_path, left=left)) == 2
    assert "tzdata_version" in json.loads(capsys.readouterr().out)["reason"]


def test_legacy_manifest_and_report_shape_are_unchanged(tmp_path, capsys):
    case = {
        "name": "legacy",
        "left": {"engine": "croniter", "expression": "0 9 * * mon", "version": None},
        "right": {"engine": "apscheduler", "expression": "0 9 * * mon"},
        "timezone": "UTC",
        "start": "2026-10-01T00:00:00Z",
        "end": "2026-10-08T00:00:00Z",
    }
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": [case]}))
    assert main(["compare", "--manifest", str(path), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 1 and report["cases"][0]["schema_version"] == 1
    single = report["cases"][0]
    assert {
        "schema_version", "name", "outcome", "reason", "profiles", "window", "completeness",
        "limits", "termination_reason", "occurrence_counts", "empty_window", "first_divergence",
        "context", "timezone_provenance", "adapter_diagnostics",
    } <= set(single)  # fmt: skip
    assert single["timezone_data"]["status"] == "identical"
    assert not single["timezone_data"]["different_tzdata_allowed"]
    assert single["profiles"]["left"]["requested"]["tzdata_version"] is None
    assert Case.parse(case).allow_different_tzdata is False


# --- optional integration with the pinned lab (see docs/tzdata-upgrade.md) ----------------

LAB = ("CRONDELTA_TZDATA_OLD_PYTHON", "CRONDELTA_TZDATA_NEW_PYTHON")


@pytest.mark.skipif(not all(os.environ.get(v) for v in LAB), reason="lab prepared by verify.sh")
@pytest.mark.parametrize(
    "timezone,outcome,left_utc,right_utc",
    [
        ("America/Havana", "DIFFERENT", "2026-11-02T14:00", "2026-11-02T13:00"),
        ("Europe/Berlin", "MATCH_WITHIN_WINDOW", "2026-11-02T08:00", "2026-11-02T08:00"),
        ("UTC", "MATCH_WITHIN_WINDOW", "2026-11-02T09:00", "2026-11-02T09:00"),
    ],
)
def test_pinned_lab_environments(make_case, timezone, outcome, left_utc, right_utc):
    case = make_case(
        "0 9 * * *", left_engine="croniter", right_engine="croniter", timezone=timezone, **WINDOW
    )
    case = replace(
        case,
        left=replace(
            case.left, python=os.environ[LAB[0]], tzdata_version="2026.5", version="6.2.4"
        ),
        right=replace(
            case.right, python=os.environ[LAB[1]], tzdata_version="2026.6", version="6.2.4"
        ),
        allow_different_tzdata=True,
    )
    result = compare(case)
    assert result["outcome"] == outcome
    assert result["completeness"]["window_checked"]
    assert utcs(result, "left")[0] == left_utc and utcs(result, "right")[0] == right_utc
    assert result["timezone_provenance"]["left"]["tzdata_version"] == "2026.5"
    assert result["timezone_provenance"]["right"]["tzdata_version"] == "2026.6"
