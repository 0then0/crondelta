import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from crondelta.adapters import run_adapter
from crondelta.comparison import compare
from crondelta.config import Case

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("engine", ["croniter", "apscheduler"])
def test_source_api_control_probe_matches_datetime_adapter(make_case, engine):
    target = os.environ.get("CRONDELTA_HISTORICAL_PYTHON", sys.executable)
    output = subprocess.check_output(
        [target, "-I", str(ROOT / "scripts/query_exporter_probe.py"), "--engine", engine],
        text=True,
    )
    results = json.loads(output)
    for result in results:
        case = make_case(
            result["expression"],
            left_engine=engine,
            end="2026-11-01T00:00:00Z"
            if "1 * mon" in result["expression"]
            else "2026-10-08T00:00:00Z",
        )
        profile = replace(case.left, python=target, version=result["actual_version"])
        stream = run_adapter(case, profile)
        assert stream.complete
        assert [e["utc"] for e in stream.events] == result["occurrences"]
        assert stream.profile["timezone"]["tzif_sha256"] == result["tzif_sha256"]
    if engine == "croniter":
        assert len(results[0]["occurrences"]) == 5
        assert results[1]["occurrences"] == ["2026-10-04T09:00:00.000000Z"]
    else:
        assert results[0]["occurrences"] == []
        assert results[1]["occurrences"] == ["2026-10-05T09:00:00.000000Z"]
    assert results[2]["occurrences"] == ["2026-10-05T09:00:00.000000Z"]


@pytest.mark.skipif(
    not os.environ.get("CRONDELTA_HISTORICAL_PYTHON"),
    reason="historical environment prepared by verify.sh",
)
def test_two_versions_of_same_engine(make_case):
    case = make_case("0 9 * * mon", right_engine="croniter")
    case = replace(
        case,
        left=replace(case.left, version="6.2.4"),
        right=replace(
            case.right, python=os.environ["CRONDELTA_HISTORICAL_PYTHON"], version="6.0.0"
        ),
    )
    result = compare(case)
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert result["profiles"]["left"]["resolved"]["actual_version"] == "6.2.4"
    assert result["profiles"]["right"]["resolved"]["actual_version"] == "6.0.0"


@pytest.mark.skipif(
    not os.environ.get("CRONDELTA_HISTORICAL_PYTHON"),
    reason="historical environment prepared by verify.sh",
)
@pytest.mark.parametrize(
    "name,outcome,counts",
    [
        ("croniter_upgrade_zurich_fold", "DIFFERENT", {"left": 3, "right": 15}),
        ("croniter_upgrade_utc_control", "MATCH_WITHIN_WINDOW", {"left": 15, "right": 15}),
    ],
)
def test_croniter_upgrade(name, outcome, counts):
    manifest = json.loads((ROOT / "examples/croniter-upgrade.json").read_text())
    assert manifest["schema_version"] == 1
    case = Case.parse(next(item for item in manifest["cases"] if item["name"] == name))
    case = replace(
        case,
        left=replace(case.left, python=os.environ["CRONDELTA_HISTORICAL_PYTHON"]),
        right=replace(case.right, python=sys.executable),
    )
    result = compare(case)
    assert result["schema_version"] == 1
    assert result["outcome"] == outcome
    assert result["completeness"]["window_checked"]
    assert result["occurrence_counts"] == counts
    assert result["timezone_provenance"]["left"] == result["timezone_provenance"]["right"]
    assert result["timezone_provenance"]["left"]["tzdata_version"] == "2026.2"
    assert result["timezone_provenance"]["left"]["zoneinfo_tzpath"] == []
    for side, version in (("left", "6.0.0"), ("right", "6.2.4")):
        profile = result["profiles"][side]
        assert profile["requested"]["engine"] == profile["resolved"]["engine"] == "croniter"
        assert profile["requested"]["version"] == profile["resolved"]["actual_version"] == version
        assert result["completeness"][side]["complete"]
        first = result["context"][side][0]
        assert first["index"] == 0
        assert first["utc"] == "2023-10-29T00:55:00.000000Z"
    assert (
        result["profiles"]["left"]["resolved"]["interpreter"]
        != result["profiles"]["right"]["resolved"]["interpreter"]
    )
    if case.timezone == "UTC":
        assert result["first_divergence"] is None
        return
    witness = result["first_divergence"]
    assert witness["kind"] == "occurrence"
    assert witness["index"] == 1
    assert witness["left"]["utc"] == "2023-10-29T02:00:00.000000Z"
    assert witness["left"]["local"] == "2023-10-29T03:00:00.000000+01:00"
    assert witness["left"]["offset_seconds"] == 3600
    assert witness["left"]["fold"] == 0
    assert witness["right"]["utc"] == "2023-10-29T01:00:00.000000Z"
    assert witness["right"]["local"] == "2023-10-29T02:00:00.000000+01:00"
    assert witness["right"]["offset_seconds"] == 3600
    assert witness["right"]["fold"] == 1
    assert witness["right"]["roundtrip_local"] == witness["right"]["local"]
    assert witness["right"]["roundtrip_fold"] == 1
    assert not witness["right"]["nonexistent_local"]
