"""Controlled reproduction of query-exporter scheduling calls, for UTC cases only.

Run this standalone script through the historical interpreter. It imports neither
query-exporter nor CronDelta. Time and timezone are injected, and no SQL runs.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.resources
import io
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, reset_tzpath


def probe(engine, expression, start, end):
    raw = importlib.resources.files("tzdata.zoneinfo").joinpath("UTC").read_bytes()
    reset_tzpath(())
    ZoneInfo.clear_cache()
    occurrences = []
    if engine == "croniter":
        from croniter import croniter
        from dateutil.tz import tzfile

        # The original obtains a dateutil tzinfo via gettz(). Inject the same type
        # from the pinned TZif bytes and replace datetime.now() with fixed start.
        timezone = tzfile(io.BytesIO(raw))
        cron_iter = croniter(expression, start.astimezone(timezone))

        def next_datetime():
            # Original executor uses next(cron_iter), which returns float seconds.
            # These UTC minute controls are exactly representable in that API.
            return datetime.fromtimestamp(next(cron_iter), UTC)

        version = importlib.metadata.version("croniter")
    else:
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger.from_crontab(expression, timezone=ZoneInfo("UTC"))
        previous = None

        def next_datetime():
            nonlocal previous
            previous = trigger.get_next_fire_time(previous, previous or start)
            return previous.astimezone(UTC) if previous else None

        version = importlib.metadata.version("APScheduler")
    while len(occurrences) < 100:
        value = next_datetime()
        if value is None or value >= end:
            break
        assert value > start, "controls must have no occurrence on the initial boundary"
        occurrences.append(value.isoformat(timespec="microseconds").replace("+00:00", "Z"))
    else:
        raise RuntimeError("control probe cap exceeded")
    return {
        "engine": engine,
        "actual_version": version,
        "expression": expression,
        "tzdata_version": importlib.metadata.version("tzdata"),
        "tzif_sha256": hashlib.sha256(raw).hexdigest(),
        "occurrences": occurrences,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("croniter", "apscheduler"), required=True)
    args = parser.parse_args()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    print(
        json.dumps(
            [
                probe(args.engine, "0 9 1 * mon", start, datetime(2026, 11, 1, tzinfo=UTC)),
                probe(args.engine, "0 9 * * 0", start, datetime(2026, 10, 8, tzinfo=UTC)),
                probe(args.engine, "0 9 * * mon", start, datetime(2026, 10, 8, tzinfo=UTC)),
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
