import io
import json
import os
import sys
import time
from dataclasses import asdict

import pytest
from conftest import event

from crondelta import _worker
from crondelta.adapters import run_adapter
from crondelta.comparison import compare_streams


def record(kind, **values):
    return {"protocol_version": 1, "type": kind, **values}


def header(case):
    return record(
        "profile",
        profile={
            **asdict(case.left),
            "requested_version": case.left.version,
            "actual_version": "6.2.4",
            "interpreter": sys.executable,
            "runtime": {"implementation": "CPython", "version": "3.13"},
            "timezone": {
                "provider": "tzdata",
                "key": "UTC",
                "tzdata_version": "2026.2",
                "tzif_sha256": "a" * 64,
                "zoneinfo_tzpath": [],
            },
        },
    )


def command(messages, suffix=""):
    lines = "".join(json.dumps(message) + "\n" for message in messages)
    return [
        sys.executable,
        "-c",
        f"import sys,time; sys.stdin.read(); sys.stdout.write({lines!r}); "
        f"sys.stdout.flush(); {suffix}",
    ]


@pytest.mark.parametrize("minutes,status", [([1, 1], "non_advancing"), ([2, 1], "non_monotonic")])
def test_non_advancing_and_non_monotonic(make_case, minutes, status):
    case = make_case()
    messages = [
        header(case),
        record("accepted"),
        *[record("occurrence", occurrence=event(i)) for i in minutes],
        record("end", status="complete"),
    ]
    stream = run_adapter(case, case.left, command=command(messages))
    assert stream.status == status
    assert len(stream.events) == 1
    assert compare_streams(stream, stream)[0] == "ENGINE_ERROR"


@pytest.mark.parametrize(
    "text",
    [
        "not json\n",
        '{"protocol_version":999,"type":"end"}\n',
        '{"protocol_version":1',
        '{"protocol_version":1,"type":"unknown"}\n',
        "[]\n",
        "\n",
        '{"protocol_version":true,"type":"end","status":"engine_error"}\n',
        "[" * 2000 + "]" * 2000 + "\n",
    ],
)
def test_invalid_adapter_json(make_case, text):
    case = make_case()
    stream = run_adapter(case, case.left, command=[sys.executable, "-c", f"print({text!r},end='')"])
    assert stream.status == "protocol_error"


def test_timeout_retains_checked_prefix(make_case):
    case = make_case(limits={"deadline_seconds": 0.3})
    messages = [header(case), record("accepted"), record("occurrence", occurrence=event(0))]
    before = time.monotonic()
    stream = run_adapter(case, case.left, command=command(messages, "time.sleep(60)"))
    assert time.monotonic() - before < 3
    assert stream.status == "timeout"
    assert len(stream.events) == 1
    assert stream.returncode is not None
    assert compare_streams(stream, stream)[0] == "UNRESOLVED"


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
def test_output_cap(make_case, pipe):
    case = make_case(limits={"stdout_bytes": 1024, "stderr_bytes": 1024})
    output = f"sys.{pipe}.write('x'*50000); sys.{pipe}.flush(); time.sleep(60)"
    stream = run_adapter(case, case.left, command=command([], output))
    assert stream.status == "output_cap"
    assert pipe in stream.detail
    assert len(stream.stderr) <= 1024
    assert stream.returncode is not None


def test_child_exit_failure_even_with_terminal_response(make_case):
    case = make_case()
    messages = [header(case), record("accepted"), record("end", status="complete")]
    stream = run_adapter(case, case.left, command=command(messages, "sys.exit(7)"))
    assert stream.status == "engine_error"
    assert stream.returncode == 7


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
@pytest.mark.parametrize("exit_code,status", [(7, "engine_error"), (0, "timeout")])
def test_exited_child_with_pipe_holding_descendant(make_case, exit_code, status):
    case = make_case(limits={"deadline_seconds": 0.5})
    messages = [header(case), record("accepted"), record("end", status="complete")]
    suffix = (
        "import subprocess; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"sys.exit({exit_code})"
    )
    before = time.monotonic()
    stream = run_adapter(case, case.left, command=command(messages, suffix))
    assert time.monotonic() - before < 3
    assert stream.status == status
    assert stream.returncode == exit_code
    assert compare_streams(stream, stream)[0] == ("ENGINE_ERROR" if exit_code else "UNRESOLVED")


@pytest.mark.parametrize("exception", [RuntimeError, FileNotFoundError])
def test_unexpected_engine_exception_is_not_parser_rejection(
    make_case, monkeypatch, capsys, exception
):
    from apscheduler.triggers.cron import CronTrigger

    def crash(*args, **kwargs):
        raise exception("synthetic unexpected engine failure")

    monkeypatch.setattr(CronTrigger, "from_crontab", crash)
    case = make_case()
    # Direct worker call verifies the real engine API's unexpected exception path.
    with pytest.raises(exception):
        _worker.run(case.request(case.right))
    assert "rejected" not in capsys.readouterr().out
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(case.request(case.right)).encode()))
    )
    _worker.main()
    captured = capsys.readouterr()
    terminal = json.loads(captured.out.splitlines()[-1])
    assert terminal["status"] == "engine_error"
    assert exception.__name__ in captured.err
    stream = run_adapter(
        case,
        case.left,
        command=command([record("end", status="engine_error", detail="synthetic failure")]),
    )
    assert compare_streams(stream, stream)[0] == "ENGINE_ERROR"


def test_inconsistent_evidence(make_case):
    case = make_case()
    bad = event(1) | {"offset_seconds": 3600}
    stream = run_adapter(
        case,
        case.left,
        command=command([header(case), record("accepted"), record("occurrence", occurrence=bad)]),
    )
    assert stream.status == "protocol_error"


def test_timezone_provenance_mismatch(make_case):
    case = make_case()
    messages = [header(case), record("accepted"), record("end", status="complete")]
    left = run_adapter(case, case.left, command=command(messages))
    messages[0]["profile"]["timezone"]["tzdata_version"] = "2025.1"
    right = run_adapter(case, case.left, command=command(messages))
    assert compare_streams(left, right)[0] == "UNRESOLVED"


def test_deadline_includes_stdin_backpressure(make_case):
    from dataclasses import replace

    case = make_case(limits={"deadline_seconds": 0.3})
    profile = replace(case.left, python="ж" * 4000)
    # Larger than typical pipe capacity. The subprocess never reads stdin.
    assert len(json.dumps(case.request(profile)).encode()) > 24000
    before = time.monotonic()
    stream = run_adapter(
        case, profile, command=[sys.executable, "-c", "import time; time.sleep(60)"]
    )
    assert stream.status == "timeout"
    assert time.monotonic() - before < 3


def test_engine_missing_interpreter(make_case):
    case = make_case()
    stream = run_adapter(case, case.left, command=["/missing/crondelta-python"])
    assert stream.status == "engine_error"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
def test_timeout_kills_pipe_holding_descendant(make_case):
    case = make_case(limits={"deadline_seconds": 0.3})
    code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    )
    before = time.monotonic()
    result = run_adapter(case, case.left, command=[sys.executable, "-c", code])
    assert result.status == "timeout"
    assert time.monotonic() - before < 3
