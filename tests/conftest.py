from datetime import UTC, datetime

import pytest

from crondelta.config import Case


@pytest.fixture
def make_case():
    def make(
        expression="0 9 * * *",
        *,
        right=None,
        timezone="UTC",
        start="2026-10-01T00:00:00Z",
        end="2026-10-08T00:00:00Z",
        left_engine="croniter",
        right_engine="apscheduler",
        left_options=None,
        right_options=None,
        limits=None,
        **overrides,
    ):
        data = {
            "name": "test",
            "timezone": timezone,
            "start": start,
            "end": end,
            "left": {
                "engine": left_engine,
                "expression": expression,
                "options": left_options or {},
            },
            "right": {
                "engine": right_engine,
                "expression": right or expression,
                "options": right_options or {},
            },
            "limits": limits or {},
        }
        data.update(overrides)
        return Case.parse(data)

    return make


def event(minute):
    value = datetime(2026, 10, 1, 0, minute, tzinfo=UTC)
    return {
        "utc": value.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "local": value.isoformat(timespec="microseconds"),
        "offset_seconds": 0,
        "fold": 0,
        "roundtrip_local": value.isoformat(timespec="microseconds"),
        "roundtrip_fold": 0,
        "nonexistent_local": False,
    }
