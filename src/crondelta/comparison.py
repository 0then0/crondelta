"""Compare ordered UTC prefixes without inferring unknown suffixes."""

import json
from dataclasses import asdict

from .adapters import Stream, run_adapter
from .config import NODE_ENGINE, Case, iso, utc_datetime

EXIT_CODES = {
    "MATCH_WITHIN_WINDOW": 0,
    "DIFFERENT": 1,
    "INVALID_INPUT": 2,
    "UNRESOLVED": 3,
    "ENGINE_ERROR": 4,
}
ERRORS = {"engine_error", "protocol_error", "missing_dependency", "non_advancing", "non_monotonic"}


ALLOW_HINT = "pass --allow-different-tzdata (manifest: allow_different_tzdata) to compare them"


def is_node(stream: Stream) -> bool:
    return bool(stream.profile) and stream.profile["engine"] == NODE_ENGINE


def runtime_identity(profile: dict) -> tuple:
    """What determines a Node.js run's ICU timezone data: the executable and its versions."""
    runtime = profile["runtime"]
    return tuple(
        json.dumps(runtime[key], sort_keys=True)
        for key in ("executable_realpath", "versions", "platform", "arch", "icu_environment")
    )


def tzdata_identity(stream: Stream) -> tuple | None:
    """The timezone data actually used, never the request.

    Python: tzdata package version and TZif bytes. Node.js exposes neither; the identity is
    the runtime (executable, ICU, and the timezone data version it reports).
    """
    if not stream.profile:
        return None
    if is_node(stream):
        return runtime_identity(stream.profile)
    tz = stream.profile["timezone"]
    return tz["tzdata_version"], tz["tzif_sha256"]


def tzdata_differs(left: Stream, right: Stream) -> bool | None:
    a, b = tzdata_identity(left), tzdata_identity(right)
    return None if a is None or b is None else a != b


def describe_runtime_difference(left: Stream, right: Stream) -> str:
    sides = []
    for side, stream in (("left", left), ("right", right)):
        runtime, tz = stream.profile["runtime"], stream.profile["timezone"]
        sides.append(
            f"{side} Node.js {runtime['version']} at {runtime['executable_realpath']}, "
            f"ICU {tz['icu_version'] or 'unknown'}, tz data {tz['tz_data_version'] or 'unknown'}"
        )
    return "; ".join(sides)


def describe_tzdata_difference(left: Stream, right: Stream) -> str:
    (lv, lh), (rv, rh) = tzdata_identity(left), tzdata_identity(right)
    hashes = "equal" if lh == rh else "different"
    return f"left tzdata {lv}, right tzdata {rv}; TZif SHA-256 {hashes}"


def unresolved_detail(left: Stream, right: Stream) -> str:
    notes = []
    for side, stream in (("left", left), ("right", right)):
        if stream.status == "version_mismatch":
            notes.append(f"{side}: requested engine version is not installed")
        elif stream.status == "tzdata_mismatch":
            tz = stream.profile["timezone"]
            notes.append(
                f"{side}: requested tzdata {tz['requested_tzdata_version']}, "
                f"installed {tz['tzdata_version']}"
            )
        elif stream.status == "unsupported":
            notes.append(f"{side}: unsupported engine version")
    return "requested engine profile cannot be resolved (" + "; ".join(notes) + ")"


def compare_streams(
    left: Stream, right: Stream, *, allow_different_tzdata: bool = False
) -> tuple[str, dict | None, str]:
    if left.status in ERRORS or right.status in ERRORS:
        return "ENGINE_ERROR", None, "adapter failure or invalid occurrence stream"
    missing = [
        f"{side}: {stream.detail}"
        for side, stream in (("left", left), ("right", right))
        if stream.status == "invalid_timezone"
    ]
    if missing:
        # One database lacking a zone that the other has is a tzdata comparison result that
        # cannot be computed; no zone from the other side is substituted.
        if len(missing) == 1 and allow_different_tzdata:
            return (
                "UNRESOLVED",
                None,
                "timezone unavailable in one explicitly selected tzdata (" + missing[0] + ")",
            )
        return (
            "INVALID_INPUT",
            None,
            "timezone unavailable in the explicitly selected tzdata (" + "; ".join(missing) + ")",
        )
    unresolved = {"version_mismatch", "tzdata_mismatch", "unsupported"}
    if left.status in unresolved or right.status in unresolved:
        return "UNRESOLVED", None, unresolved_detail(left, right)
    if is_node(left) and tzdata_differs(left, right):
        # Both sides must run in one Node.js runtime; there is no permission flag for this.
        return (
            "UNRESOLVED",
            None,
            f"different Node.js runtime ({describe_runtime_difference(left, right)}); "
            "both sides must use the same executable",
        )
    if tzdata_differs(left, right) and not allow_different_tzdata:
        return (
            "UNRESOLVED",
            None,
            f"different timezone data ({describe_tzdata_difference(left, right)}); {ALLOW_HINT}"
            " explicitly",
        )
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


CONFIGURATION_FACTORS = ("engine", "expression", "options")
TZDATA_FACTORS = {"tzdata_version", "tzif_sha256"}


def timezone_data(case: Case, left: Stream, right: Stream) -> dict:
    differs = tzdata_differs(left, right)
    identity = [tzdata_identity(stream) for stream in (left, right)]
    known = all(identity)
    if case.left.is_node:
        # No tzdata package or TZif bytes exist for Node.js; only runtime equality is known.
        return {
            "different_tzdata_allowed": False,
            "status": "unresolved"
            if differs is None
            else "different"
            if differs
            else "same_runtime",
            "versions_equal": None,
            "tzif_equal": None,
            "runtime_equal": None if differs is None else not differs,
        }
    return {
        "different_tzdata_allowed": case.allow_different_tzdata,
        "status": "unresolved" if differs is None else "different" if differs else "identical",
        "versions_equal": identity[0][0] == identity[1][0] if known else None,
        "tzif_equal": identity[0][1] == identity[1][1] if known else None,
    }


def node_profile_differences(case: Case, left: Stream, right: Stream) -> dict:
    """Report v3: tracked factors of two installations in one Node.js runtime.

    Installation paths are not factors. A difference of the Luxon that cron-parser loaded is
    listed separately, because the result cannot then be attributed to cron-parser alone.
    """
    configuration = [
        name
        for name in CONFIGURATION_FACTORS
        if getattr(case.left, name) != getattr(case.right, name)
    ]
    provenance = None
    if left.profile and right.profile:
        a, b = left.profile, right.profile
        provenance = []
        if a["actual_version"] != b["actual_version"]:
            provenance.append("engine_version")
        if runtime_identity(a) != runtime_identity(b):
            provenance.append("runtime")
        if a["loaded"]["luxon"]["version"] != b["loaded"]["luxon"]["version"]:
            provenance.append("luxon_version")
    return {
        "configuration": configuration,
        "provenance": provenance,
        "engine_version_only": None
        if provenance is None
        else not configuration and provenance == ["engine_version"],
    }


def profile_differences(case: Case, left: Stream, right: Stream) -> dict:
    """Name differing profile factors; interpreter paths and pins are not schedule factors."""
    if case.left.is_node:
        return node_profile_differences(case, left, right)
    configuration = [
        name
        for name in CONFIGURATION_FACTORS
        if getattr(case.left, name) != getattr(case.right, name)
    ]
    provenance = None
    if left.profile and right.profile:
        a, b = left.profile, right.profile
        provenance = []
        if a["actual_version"] != b["actual_version"]:
            provenance.append("engine_version")
        if a["runtime"] != b["runtime"]:
            provenance.append("runtime")
        for name in ("tzdata_version", "tzif_sha256"):
            if a["timezone"][name] != b["timezone"][name]:
                provenance.append(name)
    if configuration:
        timezone_data_only = False
    elif provenance is None:
        timezone_data_only = None
    else:
        timezone_data_only = bool(provenance) and set(provenance) <= TZDATA_FACTORS
    return {
        "configuration": configuration,
        "provenance": provenance,
        "timezone_data_only": timezone_data_only,
    }


def report(case: Case, left: Stream, right: Stream) -> dict:
    outcome, witness, reason = compare_streams(
        left, right, allow_different_tzdata=case.allow_different_tzdata
    )
    index = witness.get("index", 0) if witness else 0
    radius = case.limits.context

    def context(stream):
        low = max(0, index - radius)
        high = index + radius + 1
        return [{"index": i, **event} for i, event in enumerate(stream.events[low:high], low)]

    return {
        # Version 2 reports can pair MATCH_WITHIN_WINDOW/DIFFERENT with different tz data.
        # Version 3: Node.js runs, whose timezone data identity is the ICU runtime.
        "schema_version": 3 if case.left.is_node else 2 if case.allow_different_tzdata else 1,
        "name": case.name,
        "outcome": outcome,
        "reason": reason,
        "profiles": {
            "left": {"requested": case.left.to_dict(), "resolved": left.profile},
            "right": {"requested": case.right.to_dict(), "resolved": right.profile},
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
        "timezone_data": timezone_data(case, left, right),
        "profile_differences": profile_differences(case, left, right),
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
        "timezone_data": None,
        "profile_differences": None,
        "adapter_diagnostics": {},
    }
