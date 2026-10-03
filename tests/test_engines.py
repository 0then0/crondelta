from dataclasses import replace

import pytest

from crondelta.adapters import run_adapter
from crondelta.comparison import compare, compare_streams
from crondelta.config import utc_datetime


def streams(case):
    return run_adapter(case, case.left), run_adapter(case, case.right)


@pytest.mark.parametrize(
    "expression", ["0 9 * * *", "0 9 * * mon", "*/15 * * * *", "0 9 * jan mon"]
)
def test_simple_and_named_weekdays(make_case, expression):
    case = make_case(expression)
    result = compare(case)
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert result["completeness"]["window_checked"]
    assert result["timezone_provenance"]["left"] == result["timezone_provenance"]["right"]
    assert result["timezone_provenance"]["left"]["zoneinfo_tzpath"] == []


def test_numeric_weekday(make_case):
    left, right = streams(make_case("0 9 * * 0"))
    outcome, witness, _ = compare_streams(left, right)
    assert outcome == "DIFFERENT"
    assert witness["left"]["utc"] == "2026-10-04T09:00:00.000000Z"
    assert witness["right"]["utc"] == "2026-10-05T09:00:00.000000Z"


def test_dom_dow_and_options(make_case):
    case = make_case("0 9 1 * mon", end="2026-11-01T00:00:00Z")
    left, right = streams(case)
    assert [x["utc"][8:10] for x in left.events] == ["01", "05", "12", "19", "26"]
    assert right.events == [] and right.complete
    assert compare_streams(left, right)[0] == "DIFFERENT"
    assert (
        compare(
            make_case("0 9 1 * mon", left_options={"day_or": False}, end="2026-11-01T00:00:00Z")
        )["outcome"]
        == "MATCH_WITHIN_WINDOW"
    )


def test_different_expressions_match_in_finite_window(make_case):
    result = compare(make_case("0 9 * * *", right="0 9 1-31 * *"))
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"


@pytest.mark.parametrize("engine", ["croniter", "apscheduler"])
@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-10-01T09:00:00Z", "2026-10-02T09:00:00Z", ["01"]),
        ("2026-10-01T09:00:00.000001Z", "2026-10-03T09:00:00Z", ["02"]),
        ("2026-10-01T08:59:59.999999Z", "2026-10-01T09:00:00.000001Z", ["01"]),
        ("2026-10-01T09:00:00Z", "2026-10-01T09:00:00.000001Z", ["01"]),
    ],
)
def test_exact_boundaries(make_case, engine, start, end, expected):
    case = make_case(start=start, end=end, left_engine=engine)
    stream = run_adapter(case, case.left)
    assert stream.complete
    assert [x["utc"][8:10] for x in stream.events] == expected


def test_leap_year_rare_schedule(make_case):
    case = make_case("0 0 29 2 *", start="2027-01-01T00:00:00Z", end="2029-01-01T00:00:00Z")
    left, right = streams(case)
    assert compare_streams(left, right)[0] == "MATCH_WITHIN_WINDOW"
    assert [e["utc"] for e in left.events] == ["2028-02-29T00:00:00.000000Z"]


def test_empty_window(make_case):
    result = compare(make_case("0 0 1 1 *"))
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert result["occurrence_counts"] == {"left": 0, "right": 0}
    assert result["empty_window"]


def test_search_limit_is_not_empty_success(make_case):
    case = make_case(
        "0 0 29 2 *",
        start="2029-01-01T00:00:00Z",
        end="2029-01-02T00:00:00Z",
        left_options={"max_years_between_matches": 1},
    )
    result = compare(case)
    assert result["outcome"] == "UNRESOLVED"
    assert result["termination_reason"]["left"] == "search_limit"
    assert result["occurrence_counts"]["left"] is None


def test_berlin_gap_preserves_original_and_roundtrip(make_case):
    case = make_case(
        "30 2 * * *",
        timezone="Europe/Berlin",
        start="2026-03-28T00:00:00Z",
        end="2026-03-31T00:00:00Z",
    )
    result = compare(case)
    assert result["outcome"] == "DIFFERENT"
    witness = result["first_divergence"]
    assert witness["left"]["utc"] == "2026-03-29T01:00:00.000000Z"
    assert witness["right"]["utc"] == "2026-03-29T01:30:00.000000Z"
    assert witness["right"]["local"] == "2026-03-29T02:30:00.000000+01:00"
    assert witness["right"]["roundtrip_local"] == "2026-03-29T03:30:00.000000+02:00"
    assert witness["right"]["nonexistent_local"]


def test_berlin_fold_negative_control(make_case):
    case = make_case(
        "30 2 * * *",
        timezone="Europe/Berlin",
        start="2026-10-24T00:00:00Z",
        end="2026-10-27T00:00:00Z",
    )
    left, right = streams(case)
    assert compare_streams(left, right)[0] == "MATCH_WITHIN_WINDOW"
    for stream in (left, right):
        repeated = [e for e in stream.events if e["local"].startswith("2026-10-25T02:30")]
        assert [e["fold"] for e in repeated] == [0, 1]
        assert len({e["utc"] for e in repeated}) == 2


def test_lord_howe_half_hour_gap(make_case):
    case = make_case(
        "15 2 * * *",
        timezone="Australia/Lord_Howe",
        start="2026-10-03T00:00:00Z",
        end="2026-10-06T00:00:00Z",
    )
    left, right = streams(case)
    assert left.complete and right.complete
    assert compare_streams(left, right)[0] == "DIFFERENT"
    assert right.events[0]["nonexistent_local"]
    assert right.events[0]["offset_seconds"] == 37800
    assert right.events[0]["roundtrip_local"].endswith("+11:00")


def test_count_difference_after_equal_prefix(make_case):
    case = make_case("0 9 * * *", right="0 9 1 * *")
    result = compare(case)
    assert result["outcome"] == "DIFFERENT"
    assert result["first_divergence"]["index"] == 1
    assert result["first_divergence"]["right"] is None


def test_parser_acceptance_and_both_reject(make_case):
    # croniter supports last-day syntax; APScheduler's crontab parser does not.
    result = compare(make_case("0 9 L * *", end="2026-11-01T00:00:00Z"))
    assert result["outcome"] == "DIFFERENT"
    assert result["first_divergence"]["kind"] == "parser_acceptance"
    assert compare(make_case("99 9 * * *"))["outcome"] == "INVALID_INPUT"


def test_cap_and_exact_cap_completion(make_case):
    result = compare(make_case("* * * * *", limits={"max_occurrences": 2}))
    assert result["outcome"] == "UNRESOLVED"
    assert result["completeness"]["left"]["observed_count"] == 2
    assert result["occurrence_counts"]["left"] is None
    result = compare(
        make_case(
            "* * * * *",
            start="2026-10-01T00:00:00Z",
            end="2026-10-01T00:02:00Z",
            limits={"max_occurrences": 2},
        )
    )
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"


def test_divergence_proven_before_completion(make_case):
    result = compare(make_case("* * * * *", right="*/2 * * * *", limits={"max_occurrences": 2}))
    assert result["outcome"] == "DIFFERENT"
    assert not result["completeness"]["window_checked"]
    assert result["first_divergence"]["index"] == 1


def test_requested_version_never_substituted(make_case):
    case = make_case()
    case = replace(case, left=replace(case.left, version="0.0.0"))
    result = compare(case)
    assert result["outcome"] == "UNRESOLVED"
    assert result["termination_reason"]["left"] == "version_mismatch"
    assert result["profiles"]["left"]["resolved"]["actual_version"] == "6.2.4"


@pytest.mark.parametrize("timezone", ["Missing/Timezone", "Europe"])
def test_missing_timezone_and_dependency(make_case, timezone):
    result = compare(make_case(timezone=timezone))
    assert result["outcome"] == "INVALID_INPUT"


def test_exact_utc_identity_different_strings(make_case):
    left, right = streams(make_case())
    right.events[0]["utc"] = right.events[0]["utc"].replace(".000000Z", "+00:00")
    assert utc_datetime(left.events[0]["utc"]) == utc_datetime(right.events[0]["utc"])
    assert compare_streams(left, right)[0] == "MATCH_WITHIN_WINDOW"
