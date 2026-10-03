"""Input validation, independent of installed engine implementations."""

import math
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


class InvalidInput(ValueError):
    pass


class UnsupportedScope(ValueError):
    pass


def utc_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value
    ):
        raise InvalidInput("timestamps must be UTC ISO 8601 with seconds and at most 6 decimals")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise InvalidInput(f"invalid timestamp: {value}") from exc


def iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def keys(data: dict, allowed: set[str], label: str) -> None:
    unknown = data.keys() - allowed
    if unknown:
        raise InvalidInput(f"unknown {label} keys: {', '.join(sorted(unknown))}")


@dataclass(frozen=True)
class Profile:
    engine: str
    expression: str
    options: dict[str, Any] = field(default_factory=dict)
    python: str = sys.executable
    version: str | None = None

    @classmethod
    def parse(cls, data: dict) -> "Profile":
        if not isinstance(data, dict):
            raise InvalidInput("profile must be an object")
        keys(data, {"engine", "expression", "options", "python", "version"}, "profile")
        engine = data.get("engine")
        if not isinstance(engine, str) or not engine:
            raise InvalidInput("profile engine must be a nonempty string")
        if engine not in ("croniter", "apscheduler"):
            raise UnsupportedScope("supported engines: croniter, apscheduler (3.x)")
        expression = data.get("expression")
        if not isinstance(expression, str) or len(expression) > 1024:
            raise InvalidInput("expression must be a string of at most 1024 characters")
        if len(expression.split()) != 5:
            raise InvalidInput("exactly five cron fields are required")
        # Nondeterministic/hash-dependent schedules have no reproducible v0.1 profile.
        if re.search(r"\b[HR](?:\b|\()", expression, re.IGNORECASE):
            raise UnsupportedScope("random/hash cron fields are outside v0.1 scope")
        options = data.get("options", {})
        if not isinstance(options, dict):
            raise InvalidInput("options must be an object")
        allowed = {"day_or", "max_years_between_matches"} if engine == "croniter" else set()
        if options.keys() - allowed:
            raise UnsupportedScope(
                f"unsupported {engine} options: {sorted(options.keys() - allowed)}"
            )
        if "day_or" in options and type(options["day_or"]) is not bool:
            raise InvalidInput("day_or must be boolean")
        years = options.get("max_years_between_matches", 50)
        if type(years) is not int or not 1 <= years <= 9999:
            raise InvalidInput("max_years_between_matches must be an integer in [1, 9999]")
        resolved_options = (
            {"day_or": options.get("day_or", True), "max_years_between_matches": years}
            if engine == "croniter"
            else {}
        )
        python = data.get("python", sys.executable)
        version = data.get("version")
        if not isinstance(python, str) or not python or len(python) > 4096:
            raise InvalidInput("python must name an available interpreter")
        if version is not None and (
            not isinstance(version, str) or not version or len(version) > 128
        ):
            raise InvalidInput("version must be a nonempty string")
        return cls(engine, expression, resolved_options, python, version)


@dataclass(frozen=True)
class Limits:
    deadline_seconds: float = 10.0
    max_occurrences: int = 10000
    stdout_bytes: int = 8 * 1024 * 1024
    stderr_bytes: int = 64 * 1024
    context: int = 2

    @classmethod
    def parse(cls, data: dict) -> "Limits":
        if not isinstance(data, dict):
            raise InvalidInput("limits must be an object")
        keys(data, set(cls.__dataclass_fields__), "limits")
        values = asdict(cls()) | data
        deadline = values["deadline_seconds"]
        if (
            type(deadline) not in (int, float)
            or not 0 < deadline <= 3600
            or not math.isfinite(deadline)
        ):
            raise InvalidInput("deadline_seconds must be finite and in (0, 3600]")
        bounds = {
            "max_occurrences": (1, 1000000),
            "stdout_bytes": (256, 64 * 1024 * 1024),
            "stderr_bytes": (1, 1024 * 1024),
            "context": (0, 10),
        }
        for name, (lo, hi) in bounds.items():
            if type(values[name]) is not int or not lo <= values[name] <= hi:
                raise InvalidInput(f"{name} must be an integer in [{lo}, {hi}]")
        return cls(**values)


@dataclass(frozen=True)
class Case:
    name: str
    left: Profile
    right: Profile
    timezone: str
    start: datetime
    end: datetime
    limits: Limits

    @classmethod
    def parse(cls, data: dict) -> "Case":
        if not isinstance(data, dict):
            raise InvalidInput("case must be an object")
        keys(data, {"name", "left", "right", "timezone", "start", "end", "limits"}, "case")
        name = data.get("name", "comparison")
        timezone = data.get("timezone")
        if not isinstance(name, str) or not name or len(name) > 256:
            raise InvalidInput("name must contain 1..256 characters")
        if (
            not isinstance(timezone, str)
            or len(timezone) > 256
            or not re.fullmatch(r"[A-Za-z0-9_+\-/]+", timezone)
            or any(part in ("", ".", "..") for part in timezone.split("/"))
        ):
            raise InvalidInput("timezone must be an explicit IANA key")
        start, end = utc_datetime(data.get("start")), utc_datetime(data.get("end"))
        if start >= end:
            raise InvalidInput("start must be before end")
        # Avoid boundary overflows in public engine APIs.
        if start.year < 2 or end.year > 9998:
            raise UnsupportedScope("v0.1 supports windows within years 0002..9998")
        return cls(
            name,
            Profile.parse(data.get("left")),
            Profile.parse(data.get("right")),
            timezone,
            start,
            end,
            Limits.parse(data.get("limits", {})),
        )

    def request(self, profile: Profile) -> dict:
        return {
            "protocol_version": 1,
            "profile": asdict(profile),
            "timezone": self.timezone,
            "start": iso(self.start),
            "end": iso(self.end),
            "max_occurrences": self.limits.max_occurrences,
        }
