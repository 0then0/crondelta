"""Compare ordered UTC prefixes without inferring unknown suffixes."""

from dataclasses import asdict

from .adapters import Stream, run_adapter
from .config import Case, iso, utc_datetime

EXIT_CODES = {
    "MATCH_WITHIN_WINDOW": 0,
    "DIFFERENT": 1,
    "INVALID_INPUT": 2,
    "UNRESOLVED": 3,
    "ENGINE_ERROR": 4,
}
ERRORS = {"engine_error", "protocol_error", "missing_dependency", "non_advancing", "non_monotonic"}


def compare_streams(left: Stream, right: Stream) -> tuple[str, dict | None, str]:
    if left.status in ERRORS or right.status in ERRORS:
        return "ENGINE_ERROR", None, "adapter failure or invalid occurrence stream"
    if left.status == "invalid_timezone" or right.status == "invalid_timezone":
        return "INVALID_INPUT", None, "timezone unavailable in the explicitly selected tzdata"
    if left.status in {"version_mismatch", "unsupported"} or right.status in {
        "version_mismatch",
        "unsupported",
    }:
        return "UNRESOLVED", None, "requested engine profile cannot be resolved"
    if left.profile and right.profile:
        if left.profile["timezone"] != right.profile["timezone"]:
            return "UNRESOLVED", None, "different timezone data is outside v0.1 scope"
    if left.status == right.status == "rejected":
        return "INVALID_INPUT", None, "both engines rejected their expressions"
    if (left.status == "rejected" and right.accepted) or (
        right.status == "rejected" and left.accepted
    ):
        return (
            "DIFFERENT",
            {"kind": "parser_acceptance", "left": left.status, "right": right.status},
            "one engine accepted its expression and the other rejected it",
        )
    common = min(len(left.events), len(right.events))
    for index in range(common):
        a, b = left.events[index], right.events[index]
        if utc_datetime(a["utc"]) != utc_datetime(b["utc"]):
            return (
                "DIFFERENT",
                {"kind": "occurrence", "index": index, "left": a, "right": b},
                "first unequal UTC occurrence in checked prefixes",
            )
    if len(left.events) != len(right.events):
        shorter = left if len(left.events) < len(right.events) else right
        if shorter.complete:
            return (
                "DIFFERENT",
                {
                    "kind": "occurrence",
                    "index": common,
                    "left": left.events[common] if common < len(left.events) else None,
                    "right": right.events[common] if common < len(right.events) else None,
                },
                "one fully checked stream ends before the other's next occurrence",
            )
    if left.complete and right.complete:
        return (
            "MATCH_WITHIN_WINDOW",
            None,
            "equal UTC occurrence streams in the fully checked window",
        )
    return "UNRESOLVED", None, "no proven difference; at least one window was not fully checked"


def report(case: Case, left: Stream, right: Stream) -> dict:
    outcome, witness, reason = compare_streams(left, right)
    index = witness.get("index", 0) if witness else 0
    radius = case.limits.context

    def context(stream):
        low = max(0, index - radius)
        high = index + radius + 1
        return [{"index": i, **event} for i, event in enumerate(stream.events[low:high], low)]

    return {
        "schema_version": 1,
        "name": case.name,
        "outcome": outcome,
        "reason": reason,
        "profiles": {
            "left": {"requested": asdict(case.left), "resolved": left.profile},
            "right": {"requested": asdict(case.right), "resolved": right.profile},
        },
        "window": {
            "start": iso(case.start),
            "end": iso(case.end),
            "bounds": "[start, end)",
            "timezone": case.timezone,
        },
        "completeness": {
            "window_checked": left.complete and right.complete,
            "left": left.summary(),
            "right": right.summary(),
        },
        "limits": asdict(case.limits),
        "termination_reason": {"left": left.status, "right": right.status},
        "occurrence_counts": {
            "left": len(left.events) if left.complete else None,
            "right": len(right.events) if right.complete else None,
        },
        "empty_window": left.complete and right.complete and not left.events and not right.events,
        "first_divergence": witness,
        "context": {"left": context(left), "right": context(right)},
        "timezone_provenance": {
            "left": left.profile["timezone"] if left.profile else None,
            "right": right.profile["timezone"] if right.profile else None,
        },
        "adapter_diagnostics": {
            "left": left.summary()["diagnostics"],
            "right": right.summary()["diagnostics"],
        },
    }


def compare(case: Case) -> dict:
    return report(case, run_adapter(case, case.left), run_adapter(case, case.right))


def input_report(outcome: str, detail: str) -> dict:
    return {
        "schema_version": 1,
        "outcome": outcome,
        "reason": detail,
        "profiles": {"left": None, "right": None},
        "window": None,
        "completeness": {"window_checked": False, "left": None, "right": None},
        "limits": None,
        "termination_reason": "configuration",
        "occurrence_counts": {"left": None, "right": None},
        "empty_window": False,
        "first_divergence": None,
        "context": {"left": [], "right": []},
        "timezone_provenance": {"left": None, "right": None},
        "adapter_diagnostics": {},
    }
