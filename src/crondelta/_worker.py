"""Standalone adapter: executed by any supplied Python, without importing CronDelta.

Only stdlib and the selected engine/tzdata are required in the target interpreter.
Stdout is NDJSON protocol v1; engine diagnostics belong on stderr.
The time zone is always built from the tzdata wheel bytes that are hashed in the report.
"""

import hashlib
import importlib.metadata
import importlib.resources
import io
import json
import platform
import sys
import traceback
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, reset_tzpath


def emit(kind, **fields):
    print(json.dumps({"protocol_version": 1, "type": kind, **fields}), flush=True)


def instant(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def occurrence(local, timezone):
    utc = local.astimezone(UTC)
    roundtrip = utc.astimezone(timezone)
    return {
        "local": local.isoformat(timespec="microseconds"),
        "utc": instant(utc),
        "offset_seconds": int(local.utcoffset().total_seconds()),
        "fold": local.fold,
        "roundtrip_local": roundtrip.isoformat(timespec="microseconds"),
        "roundtrip_fold": roundtrip.fold,
        "nonexistent_local": local.replace(tzinfo=None) != roundtrip.replace(tzinfo=None),
    }


def run(request):
    if request.get("protocol_version") != 1:
        raise ValueError("unsupported request protocol")
    profile = request["profile"]
    engine = profile["engine"]
    distribution = "APScheduler" if engine == "apscheduler" else "croniter"
    version = importlib.metadata.version(distribution)
    tzversion = importlib.metadata.version("tzdata")
    # The zone is built from the very bytes that are hashed below, never from a search path
    # or the ZoneInfo cache. The empty search path additionally stops engines that resolve a
    # zone by name from reaching a system tz database.
    reset_tzpath(())
    ZoneInfo.clear_cache()
    key = request["timezone"]
    try:
        tzif = importlib.resources.files("tzdata.zoneinfo").joinpath(*key.split("/")).read_bytes()
        timezone = ZoneInfo.from_file(io.BytesIO(tzif), key=key)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError) as exc:
        detail = f"timezone {key!r} is not available in tzdata {tzversion}: {exc}"
        emit("end", status="invalid_timezone", detail=detail[:1000])
        return
    provenance = {
        "provider": "tzdata",
        "source": "tzdata.zoneinfo",
        "tzdata_version": tzversion,
        "requested_tzdata_version": profile.get("tzdata_version"),
        "tzif_sha256": hashlib.sha256(tzif).hexdigest(),
        "key": key,
        "zoneinfo_tzpath": [],
    }
    resolved = {
        **profile,
        "requested_version": profile["version"],
        "actual_version": version,
        "interpreter": sys.executable,
        "runtime": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "timezone": provenance,
    }
    emit("profile", profile=resolved)
    if profile["version"] is not None and profile["version"] != version:
        emit("end", status="version_mismatch", detail="requested engine version is not installed")
        return
    if profile.get("tzdata_version") is not None and profile["tzdata_version"] != tzversion:
        emit(
            "end",
            status="tzdata_mismatch",
            detail=f"requested tzdata {profile['tzdata_version']} but {tzversion} is installed",
        )
        return
    if engine == "apscheduler" and version.split(".")[0] != "3":
        emit("end", status="unsupported", detail="only APScheduler 3.x is supported")
        return
    start = datetime.fromisoformat(request["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(request["end"].replace("Z", "+00:00"))
    if engine == "croniter":
        from croniter import CroniterBadCronError, CroniterBadDateError, croniter

        # Five fields have minute resolution. Anchor at the preceding UTC minute,
        # enumerate forward, then filter on exact UTC start. No epsilon is used.
        anchor = start.replace(second=0, microsecond=0) - timedelta(minutes=1)
        try:
            iterator = croniter(
                profile["expression"], anchor.astimezone(timezone), **profile["options"]
            )
        except (CroniterBadCronError, ValueError) as exc:
            emit("end", status="rejected", detail=str(exc)[:1000])
            return

        def next_local():
            return iterator.get_next(datetime)

        search_errors = (CroniterBadDateError, OverflowError)
    elif engine == "apscheduler":
        from apscheduler.triggers.cron import CronTrigger

        try:
            trigger = CronTrigger.from_crontab(profile["expression"], timezone=timezone)
        except ValueError as exc:
            emit("end", status="rejected", detail=str(exc)[:1000])
            return
        previous_fire_time = None

        def next_local():
            nonlocal previous_fire_time
            now = (
                previous_fire_time if previous_fire_time is not None else start.astimezone(timezone)
            )
            result = trigger.get_next_fire_time(previous_fire_time, now)
            previous_fire_time = result
            return result

        search_errors = (OverflowError,)
    else:
        raise ValueError("unsupported engine")
    emit("accepted")
    count = 0
    previous_utc = None
    while True:
        try:
            local = next_local()
        except search_errors as exc:
            emit("end", status="search_limit", detail=str(exc)[:1000])
            return
        if local is None:
            emit("end", status="complete", detail="engine reports no future occurrence")
            return
        if local.tzinfo is None or local.utcoffset() is None:
            raise ValueError("engine returned a naive datetime")
        utc = local.astimezone(UTC)
        if previous_utc is not None and utc <= previous_utc:
            status = "non_advancing" if utc == previous_utc else "non_monotonic"
            emit("end", status=status, detail=f"{instant(previous_utc)} -> {instant(utc)}")
            return
        previous_utc = utc
        if utc >= end:
            emit("end", status="complete", detail="next occurrence is at or beyond exclusive end")
            return
        if utc < start:
            continue
        if count == request["max_occurrences"]:
            emit(
                "end", status="occurrence_cap", detail="additional occurrence exists within window"
            )
            return
        emit("occurrence", occurrence=occurrence(local, timezone))
        count += 1


def main():
    try:
        request = json.loads(sys.stdin.buffer.read(64 * 1024))
        run(request)
    except importlib.metadata.PackageNotFoundError as exc:
        emit("end", status="missing_dependency", detail=str(exc)[:1000])
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit("end", status="engine_error", detail=f"{type(exc).__name__}: {exc}"[:1000])


if __name__ == "__main__":
    main()
