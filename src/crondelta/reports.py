"""Human report rendering; JSON is the versioned report dictionary."""


def tzdata_lines(report: dict) -> list[str]:
    data, differences = report.get("timezone_data"), report.get("profile_differences")
    if not data:
        return []
    allowed = "explicitly allowed" if data["different_tzdata_allowed"] else "not allowed"
    status = data["status"]
    if status == "different":
        versions = "equal" if data["versions_equal"] else "different"
        hashes = "equal" if data["tzif_equal"] else "different"
        lines = [
            f"Timezone data: different, comparison {allowed}; tzdata versions {versions}, "
            f"TZif SHA-256 {hashes}"
        ]
    else:
        lines = [f"Timezone data: {status}"]
    configuration = differences["configuration"]
    provenance = differences["provenance"]
    if configuration or provenance:
        lines.append(
            "Differing factors: configuration "
            + (", ".join(configuration) or "none")
            + "; provenance "
            + (", ".join(provenance) if provenance is not None else "unknown")
        )
    if status == "different":
        if differences["timezone_data_only"]:
            lines.append(
                "Tracked factors other than timezone data (engine, engine version, expression, "
                "options, runtime) are equal; other dependencies are not compared."
            )
        elif data["different_tzdata_allowed"]:
            lines.append(
                "Other tracked factors also differ or are unknown; occurrence differences "
                "cannot be attributed to timezone data alone."
            )
    return lines


def human(report: dict) -> str:
    if "cases" in report:
        return (
            "\n\n".join(human(case) for case in report["cases"])
            + f"\n\nOverall: {report['outcome']}"
        )
    lines = [f"{report.get('name', 'comparison')}: {report['outcome']}", report["reason"]]
    window = report.get("window")
    if not window:
        return "\n".join(lines)
    lines.append(f"Window: [{window['start']}, {window['end']}) in {window['timezone']}")
    for side in ("left", "right"):
        requested = report["profiles"][side]["requested"]
        resolved = report["profiles"][side]["resolved"]
        version = resolved["actual_version"] if resolved else "unresolved"
        summary = report["completeness"][side]
        count = (
            summary["count"] if summary["count"] is not None else f">={summary['observed_count']}"
        )
        lines.append(
            f"{side}: {requested['engine']} {version}, {requested['expression']!r}, "
            f"occurrences={count}, {summary['termination_reason']}"
        )
        if resolved:
            tz = resolved["timezone"]
            requested_tz = tz.get("requested_tzdata_version") or "unpinned"
            lines.append(
                f"  Python {resolved['runtime']['version']}; tzdata {tz['tzdata_version']} "
                f"(requested: {requested_tz}); TZif SHA-256 {tz['tzif_sha256']}"
            )
        diagnostic = report["adapter_diagnostics"][side]
        if diagnostic["detail"]:
            lines.append(f"  {diagnostic['detail']}")
    lines.append(
        f"Window fully checked: {'yes' if report['completeness']['window_checked'] else 'no'}"
    )
    lines.extend(tzdata_lines(report))
    if report["empty_window"]:
        lines.append("Empty check: both fully enumerated streams contain zero occurrences.")
    witness = report["first_divergence"]
    if witness:
        lines.append(f"First divergence: {witness['kind']}")
        if witness["kind"] == "occurrence":
            for side in ("left", "right"):
                event = witness[side]
                if event is None:
                    lines.append(f"  {side}: no remaining occurrence in the fully checked window")
                else:
                    lines.append(
                        f"  {side}: UTC={event['utc']}, local={event['local']}, "
                        f"offset={event['offset_seconds']}s, fold={event['fold']}"
                    )
                    lines.append(
                        f"    UTC round-trip={event['roundtrip_local']}, "
                        f"fold={event['roundtrip_fold']}, "
                        f"nonexistent_local={event['nonexistent_local']}"
                    )
    for side in ("left", "right"):
        if report["context"][side]:
            lines.append(f"{side} context:")
            for event in report["context"][side]:
                lines.append(
                    f"  [{event['index']}] {event['utc']} | {event['local']} | "
                    f"fold={event['fold']} | round-trip={event['roundtrip_local']}"
                )
    if report["outcome"] == "MATCH_WITHIN_WINDOW":
        if report.get("timezone_data", {}) and report["timezone_data"]["status"] == "different":
            lines.append("Equal UTC streams despite the different timezone data listed above.")
        lines.append(
            "This is evidence only for this complete finite window, not global equivalence."
        )
    return "\n".join(lines)
