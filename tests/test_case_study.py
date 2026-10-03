import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from crondelta.adapters import run_adapter
from crondelta.comparison import compare

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
