import os
import struct
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

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


def tzif(hours, abbr="TST"):
    """Smallest valid TZif v2 file: a single fixed offset, no transitions."""
    name = abbr.encode() + b"\0"
    counts = struct.pack(">6l", 0, 0, 0, 0, 1, len(name))
    ttinfo = struct.pack(">lBB", hours * 3600, 0, 0)
    block = b"TZif2" + bytes(15) + counts + ttinfo + name
    return block + block + b"\n<" + abbr.encode() + b">" + str(-hours).encode() + b"\n"


# Synthetic tz databases. Havana's offset changes between "old" and "new" like a rule change;
# "reissued" has other bytes with the same behavior; Berlin and UTC never change.
STABLE = {"Europe/Berlin": tzif(1, "CET"), "UTC": tzif(0, "UTC")}
DATABASES = {
    "2100.1": {**STABLE, "America/Havana": tzif(-5, "CST")},
    "2100.2": {**STABLE, "America/Havana": tzif(-4, "CDT")},
    "2100.3": {**STABLE, "America/Havana": tzif(-5, "CUB")},
    "2100.4": dict(STABLE),
}
SHARED_DISTRIBUTIONS = ("croniter", "dateutil", "six", "apscheduler", "tzlocal")


@pytest.fixture(scope="session")
def tz_env(tmp_path_factory):
    """Factory for interpreters whose only tzdata is a synthetic database.

    Engines are shared through symlinks that exclude the real tzdata. Nothing is installed.
    """
    if os.name != "posix":
        pytest.skip("symlinked synthetic environments require POSIX")
    import croniter

    site = Path(croniter.__file__).resolve().parent.parent
    root = tmp_path_factory.mktemp("tz-env")
    shim = root / "engines"
    shim.mkdir()
    for entry in site.iterdir():
        stem = entry.name.replace("-", "_").split(".")[0].split("_")[0]
        if stem in SHARED_DISTRIBUTIONS or entry.name in {"six.py"}:
            (shim / entry.name).symlink_to(entry)
    cache = {}

    def make(version):
        """`version=None` creates an interpreter without any tzdata distribution."""
        if version in cache:
            return cache[version]
        venv = root / f"venv-{version}"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
        python = venv / "bin" / "python"
        purelib = subprocess.check_output(
            [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
        ).strip()
        Path(purelib, "engines.pth").write_text(str(shim) + "\n")
        if version is not None:
            package = Path(purelib, "tzdata")
            for key, data in DATABASES[version].items():
                path = package / "zoneinfo" / key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (package / "__init__.py").write_text("")
            (package / "zoneinfo" / "__init__.py").write_text("")
            dist = Path(purelib, f"tzdata-{version}.dist-info")
            dist.mkdir()
            (dist / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: tzdata\nVersion: {version}\n"
            )
        cache[version] = str(python)
        return cache[version]

    return make
