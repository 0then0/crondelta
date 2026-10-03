"""Human report rendering; JSON is the versioned report dictionary."""


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
            lines.append(
                f"  Python {resolved['runtime']['version']}; tzdata {tz['tzdata_version']}; "
                f"TZif SHA-256 {tz['tzif_sha256']}"
            )
        diagnostic = report["adapter_diagnostics"][side]
        if diagnostic["detail"]:
            lines.append(f"  {diagnostic['detail']}")
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
        lines.append(
            "This is evidence only for this complete finite window, not global equivalence."
        )
    return "\n".join(lines)
