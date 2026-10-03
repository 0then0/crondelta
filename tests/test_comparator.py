from conftest import event
from hypothesis import given
from hypothesis import strategies as st

from crondelta.adapters import Stream
from crondelta.comparison import compare_streams

prefixes = st.lists(st.integers(0, 59), unique=True).map(sorted)


def stream(minutes, complete):
    return Stream(
        events=[event(i) for i in minutes],
        accepted=True,
        status="complete" if complete else "occurrence_cap",
    )


@given(prefixes, prefixes, st.booleans(), st.booleans())
def test_symmetry_and_no_false_match(a, b, a_complete, b_complete):
    left, right = stream(a, a_complete), stream(b, b_complete)
    outcome = compare_streams(left, right)[0]
    assert compare_streams(right, left)[0] == outcome
    if outcome == "MATCH_WITHIN_WINDOW":
        assert a_complete and b_complete and a == b
    if not a_complete or not b_complete:
        assert outcome != "MATCH_WITHIN_WINDOW"


@given(prefixes, prefixes, st.booleans(), st.booleans())
def test_witness_proven_by_available_prefixes(a, b, a_complete, b_complete):
    left, right = stream(a, a_complete), stream(b, b_complete)
    outcome, witness, _ = compare_streams(left, right)
    if outcome == "DIFFERENT":
        index = witness["index"]
        assert a[:index] == b[:index]
        if witness["left"] is None:
            assert a_complete and index == len(a) and index < len(b)
        elif witness["right"] is None:
            assert b_complete and index == len(b) and index < len(a)
        else:
            assert a[index] != b[index]


def test_shorter_incomplete_prefix_has_no_absence_witness():
    assert compare_streams(stream([0], False), stream([0, 1], True))[0] == "UNRESOLVED"


def test_parser_timeout_is_not_acceptance():
    rejected = Stream(status="rejected")
    timed_out = Stream(status="timeout")
    assert compare_streams(rejected, timed_out)[0] == "UNRESOLVED"
    timed_out.accepted = True
    assert compare_streams(rejected, timed_out)[0] == "DIFFERENT"
