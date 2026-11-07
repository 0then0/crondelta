"""Bounded subprocess transport and strict adapter protocol validation."""

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Case, Profile, utc_datetime

PYTHON_WORKER = Path(__file__).with_name("_worker.py")
NODE_WORKER = Path(__file__).with_name("_node_worker.cjs")

STATUSES = {
    "complete",
    "rejected",
    "search_limit",
    "occurrence_cap",
    "version_mismatch",
    "tzdata_mismatch",
    "unsupported",
    "engine_error",
    "non_advancing",
    "non_monotonic",
    "invalid_timezone",
    "missing_dependency",
}


@dataclass
class Stream:
    profile: dict | None = None
    accepted: bool = False
    events: list[dict] = field(default_factory=list)
    status: str = "not_started"
    detail: str = ""
    stderr: str = ""
    returncode: int | None = None

    @property
    def complete(self) -> bool:
        return self.status == "complete"

    def summary(self) -> dict:
        return {
            "complete": self.complete,
            "accepted": self.accepted,
            "count": len(self.events) if self.complete else None,
            "observed_count": len(self.events),
            "termination_reason": self.status,
            "diagnostics": {
                "detail": self.detail,
                "stderr": self.stderr,
                "returncode": self.returncode,
            },
        }


def requested_mismatch(profile: dict, requested: Profile) -> str | None:
    """Terminal status that a worker must report when it cannot honor the requested pins."""
    if requested.version is not None and profile["actual_version"] != requested.version:
        return "version_mismatch"
    if (
        requested.tzdata_version is not None
        and profile["timezone"]["tzdata_version"] != requested.tzdata_version
    ):
        return "tzdata_mismatch"
    return None


def nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value)


def validate_node_provenance(profile: dict, case: Case) -> None:
    """Node.js runs have ICU timezone data: no tzdata package version and no TZif bytes."""
    runtime = profile["runtime"]
    versions = runtime.get("versions")
    if (
        not isinstance(versions, dict)
        or not all(nonempty_str(k) and isinstance(v, str) for k, v in versions.items())
        or versions.get("node") != runtime["version"]
        or not all(nonempty_str(runtime.get(k)) for k in ("platform", "arch", "executable"))
        or not nonempty_str(runtime.get("executable_realpath"))
        or not isinstance(runtime.get("icu_environment"), list)
        or not all(nonempty_str(name) for name in runtime["icu_environment"])
    ):
        raise ValueError("invalid Node.js runtime provenance")
    root = profile.get("installation_realpath")
    loaded = profile.get("loaded")
    if (
        not nonempty_str(root)
        or not isinstance(loaded, dict)
        or loaded.keys()
        != {
            "cron-parser",
            "luxon",
        }
    ):
        raise ValueError("invalid loaded module provenance")
    for module in loaded.values():
        if (
            not isinstance(module, dict)
            or not nonempty_str(module.get("version"))
            or not nonempty_str(module.get("path"))
            or not module["path"].startswith(root + os.sep)
        ):
            raise ValueError("module was not loaded from the selected installation")
    if loaded["cron-parser"]["version"] != profile["actual_version"]:
        raise ValueError("loaded cron-parser version differs from the reported version")
    tz = profile.get("timezone")
    optional = ("icu_version", "tz_data_version", "cldr_version", "unicode_version")
    if (
        not isinstance(tz, dict)
        or tz.get("provider") != "node-intl"
        or tz.get("key") != case.timezone
        or not all(nonempty_str(tz.get(k)) for k in ("source", "resolved_key", "luxon_zone_name"))
        or tz.get("luxon_zone_type") not in ("iana", "fixed")
        or not all(tz.get(k) is None or nonempty_str(tz.get(k)) for k in optional)
        or tz.get("tzdata_package_version") is not None
        or tz.get("tzif_sha256") is not None
    ):
        raise ValueError("invalid timezone provenance")
    expected_missing = {"tzdata_package_version", "tzif_sha256"} | {
        k for k in optional if tz[k] is None
    }
    if not isinstance(tz.get("unavailable"), list) or set(tz["unavailable"]) != expected_missing:
        raise ValueError("unavailable timezone provenance must be listed explicitly")


def consume(stream: Stream, message: dict, case: Case, requested: Profile) -> None:
    if (
        not isinstance(message, dict)
        or type(message.get("protocol_version")) is not int
        or message["protocol_version"] != 1
    ):
        raise ValueError("invalid protocol version/object")
    if stream.status != "running":
        raise ValueError("record after terminal response")
    kind = message.get("type")
    if kind == "profile":
        if stream.profile is not None or stream.events:
            raise ValueError("duplicate/late profile")
        profile = message.get("profile")
        if not isinstance(profile, dict):
            raise ValueError("invalid resolved profile")
        for key, expected in requested.to_dict().items():
            if profile.get(key) != expected:
                raise ValueError(f"profile changed requested {key}")
        for key in ("actual_version", "interpreter"):
            if not isinstance(profile.get(key), str) or not profile[key]:
                raise ValueError(f"missing profile {key}")
        runtime = profile.get("runtime")
        if not isinstance(runtime, dict) or not all(
            isinstance(runtime.get(k), str) and runtime[k] for k in ("version", "implementation")
        ):
            raise ValueError("invalid runtime provenance")
        tz = profile.get("timezone")
        if requested.is_node:
            validate_node_provenance(profile, case)
        elif (
            not isinstance(tz, dict)
            or tz.get("provider") != "tzdata"
            or tz.get("source") != "tzdata.zoneinfo"
            or tz.get("key") != case.timezone
            or tz.get("zoneinfo_tzpath") != []
            or not isinstance(tz.get("tzdata_version"), str)
            or not tz["tzdata_version"]
            or tz.get("requested_tzdata_version") != requested.tzdata_version
            or not isinstance(tz.get("tzif_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", tz["tzif_sha256"])
        ):
            raise ValueError("invalid timezone provenance")
        stream.profile = profile
    elif kind == "accepted":
        if stream.profile is None or stream.accepted:
            raise ValueError("invalid/duplicate acceptance record")
        if requested_mismatch(stream.profile, requested):
            raise ValueError("worker proceeded despite unmet requested engine/tzdata version")
        stream.accepted = True
    elif kind == "occurrence":
        if stream.profile is None:
            raise ValueError("occurrence before profile")
        if not stream.accepted:
            raise ValueError("occurrence before parser acceptance")
        if len(stream.events) >= case.limits.max_occurrences:
            raise ValueError("adapter exceeded occurrence cap")
        event = message.get("occurrence")
        if not isinstance(event, dict):
            raise ValueError("invalid occurrence")
        utc = utc_datetime(event.get("utc"))
        if not case.start <= utc < case.end:
            raise ValueError("occurrence outside window")
        if stream.events:
            previous = utc_datetime(stream.events[-1]["utc"])
            if utc <= previous:
                stream.status = "non_advancing" if utc == previous else "non_monotonic"
                stream.detail = f"{stream.events[-1]['utc']} -> {event['utc']}"
                return
        local = datetime.fromisoformat(event["local"])
        roundtrip = datetime.fromisoformat(event["roundtrip_local"])
        if (
            local.utcoffset() is None
            or roundtrip.utcoffset() is None
            or local.astimezone(utc.tzinfo) != utc
            or roundtrip.astimezone(utc.tzinfo) != utc
            or type(event.get("offset_seconds")) is not int
            or local.utcoffset().total_seconds() != event["offset_seconds"]
            or type(event.get("fold")) is not int
            or event["fold"] not in (0, 1)
            or type(event.get("roundtrip_fold")) is not int
            or event["roundtrip_fold"] not in (0, 1)
            or type(event.get("nonexistent_local")) is not bool
            or event["nonexistent_local"]
            != (local.replace(tzinfo=None) != roundtrip.replace(tzinfo=None))
        ):
            raise ValueError("inconsistent occurrence evidence")
        stream.events.append(event)
    elif kind == "end":
        status = message.get("status")
        if status not in STATUSES:
            raise ValueError("unknown terminal status")
        if stream.profile is None and status not in {
            "invalid_timezone",
            "missing_dependency",
            "engine_error",
        }:
            raise ValueError("terminal response without profile")
        if status == "rejected" and stream.accepted:
            raise ValueError("parser rejection after occurrences")
        if status == "complete" and not stream.accepted:
            raise ValueError("completion before parser acceptance")
        if stream.profile:
            expected = requested_mismatch(stream.profile, requested)
            if (expected or status in {"version_mismatch", "tzdata_mismatch"}) and (
                status != expected
            ):
                raise ValueError("inconsistent requested/actual version status")
        if not isinstance(message.get("detail", ""), str):
            raise ValueError("invalid diagnostic")
        stream.status = status
        stream.detail = message.get("detail", "")[:1000]
    else:
        raise ValueError("unknown record type")


def run_adapter(case: Case, profile: Profile, *, command: list[str] | None = None) -> Stream:
    """The command override is for transport tests, never exposed as a CLI plugin API."""
    stream = Stream(status="running")
    env = None
    if command is None and profile.is_node:
        command = [profile.node, str(NODE_WORKER)]
        # Options and search paths must not change which library or ICU data is loaded.
        env = {k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS", "NODE_PATH")}
    command = command or [profile.python, "-I", str(PYTHON_WORKER)]
    started = time.monotonic()
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
            start_new_session=os.name == "posix",
        )
    except (OSError, ValueError) as exc:
        stream.status, stream.detail = "engine_error", f"cannot start adapter: {exc}"[:1000]
        return stream
    chunks = queue.Queue(maxsize=32)
    cancel = threading.Event()

    def read_pipe(pipe, name):
        while not cancel.is_set():
            try:
                data = os.read(pipe.fileno(), 4096)
            except OSError:
                data = b""
            while not cancel.is_set():
                try:
                    chunks.put((name, data), timeout=0.05)
                    break
                except queue.Full:
                    continue
            if not data:
                break

    readers = [
        threading.Thread(target=read_pipe, args=(child.stdout, "stdout"), daemon=True),
        threading.Thread(target=read_pipe, args=(child.stderr, "stderr"), daemon=True),
    ]

    def write_request():
        # Keep pipe backpressure from consuming the coordinator's deadline.
        payload = json.dumps(case.request(profile)).encode()
        try:
            while payload:
                written = os.write(child.stdin.fileno(), payload)
                payload = payload[written:]
        except OSError:
            # EOF/exit and terminal protocol validation determine the outcome.
            pass
        finally:
            child.stdin.close()

    readers.append(threading.Thread(target=write_request, daemon=True))
    for reader in readers:
        reader.start()
    buffer = b""
    stderr = bytearray()
    totals = {"stdout": 0, "stderr": 0}
    closed = set()
    failure = None
    try:
        while len(closed) < 2:
            remaining = case.limits.deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                failure = ("timeout", "adapter execution deadline exceeded")
                break
            try:
                name, data = chunks.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if not data:
                closed.add(name)
                continue
            cap = case.limits.stdout_bytes if name == "stdout" else case.limits.stderr_bytes
            allowed = data[: max(0, cap - totals[name])]
            totals[name] += len(data)
            if name == "stderr":
                stderr.extend(allowed)
            else:
                buffer += allowed
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    consume(stream, json.loads(line), case, profile)
                    if stream.status in ("non_advancing", "non_monotonic"):
                        failure = (stream.status, stream.detail)
                        break
            if failure:
                break
            if totals[name] > cap:
                failure = ("output_cap", f"{name} byte cap exceeded")
                break
        if failure is None:
            if buffer or stream.status == "running":
                failure = ("protocol_error", "truncated JSON or missing terminal response")
            else:
                remaining = case.limits.deadline_seconds - (time.monotonic() - started)
                try:
                    child.wait(timeout=max(0, remaining))
                except subprocess.TimeoutExpired:
                    failure = ("timeout", "adapter did not exit before deadline")
                if child.returncode not in (None, 0):
                    failure = ("engine_error", f"adapter exited with code {child.returncode}")
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        failure = ("protocol_error", f"invalid adapter JSON: {exc}"[:1000])
    except OSError as exc:
        failure = ("engine_error", str(exc)[:1000])
    finally:
        # A crashed child can leave inherited pipes open in a descendant. Preserve
        # its natural exit before cleanup; a kill enforcing our limits is not a crash.
        natural_returncode = child.poll()
        if natural_returncode not in (None, 0):
            failure = ("engine_error", f"adapter exited with code {natural_returncode}")
        cancel.set()
        # Kill the process group even if the direct child already exited: descendants
        # may hold pipe descriptors. wait() reaps the direct child.
        if os.name == "posix":
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Some restricted hosts reject killpg for an already exited group.
                # The direct owned child still needs to be stopped and reaped.
                if child.poll() is None:
                    child.kill()
        elif child.poll() is None:
            child.kill()
        child.wait()
        for reader in readers:
            reader.join(timeout=0.2)
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()
    stream.returncode = child.returncode
    stream.stderr = stderr.decode("utf-8", errors="replace")[:2000]
    if failure:
        stream.status, stream.detail = failure
    return stream
