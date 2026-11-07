"""cron-parser comparisons through a real Node.js and real or synthetic installations.

Prepared installations (`scripts/cron_parser_lab.py prepare`) are selected by
CRONDELTA_NODE, CRONDELTA_CRON_PARSER_OLD (5.8.1) and CRONDELTA_CRON_PARSER_NEW (5.9.0);
`scripts/verify.sh` sets them. Tests of failure paths use a tiny synthetic cron-parser package
inside a temporary installation with a copy of the real Luxon, so they need the same
installations but not the network. Nothing here installs a package.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import replace
from pathlib import Path

import pytest
from test_transport import record

from crondelta.adapters import run_adapter
from crondelta.cli import main
from crondelta.comparison import compare, compare_streams
from crondelta.config import Case, InvalidInput, UnsupportedScope

NODE = os.environ.get("CRONDELTA_NODE")
OLD = os.environ.get("CRONDELTA_CRON_PARSER_OLD")
NEW = os.environ.get("CRONDELTA_CRON_PARSER_NEW")
LAB = pytest.mark.skipif(
    not (NODE and OLD and NEW), reason="cron-parser lab prepared by verify.sh is required"
)
TROLL = {
    "expression": "30 1 * * *",
    "timezone": "Antarctica/Troll",
    "start": "2026-03-27T00:00:00Z",
    "end": "2026-03-31T00:00:00Z",
}


def node_case(left=None, right=None, *, node=None, limits=None, **values):
    values = {**TROLL, **values}
    expression = values.pop("expression")

    def side(installation, version):
        return {
            "engine": "cron-parser",
            "expression": expression,
            "installation": installation,
            "version": version,
        }

    return Case.parse(
        {
            "name": "node",
            "node": node or NODE,
            "left": side(left or NEW, "5.9.0"),
            "right": side(right or NEW, "5.9.0"),
            "limits": limits or {},
            **values,
        }
    )


def events(case, installation=None, version="5.9.0"):
    profile = replace(case.left, installation=installation or case.left.installation)
    stream = run_adapter(case, replace(profile, version=version))
    assert stream.complete, (stream.status, stream.detail, stream.stderr)
    return stream.events


def utcs(items):
    return [item["utc"] for item in items]


@pytest.fixture(scope="session")
def luxon_files(tmp_path_factory):
    """The real Luxon from the prepared installation, reduced to what Node loads."""
    if not NEW:
        pytest.skip("prepared installation required")
    source = Path(NEW) / "node_modules" / "luxon"
    target = tmp_path_factory.mktemp("luxon") / "luxon"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("src", "amd", "es6", "global"))
    return target


@pytest.fixture
def synthetic(tmp_path, luxon_files):
    """Create an installation whose cron-parser is the given JavaScript.

    `parse(expression, options)` receives the real library's inputs; `at(ms)` returns a result
    shaped like cron-parser's CronDate, backed by the real Luxon.
    """

    def make(body, *, version="5.99.0", name="installation", luxon=True):
        root = tmp_path / name
        package = root / "node_modules" / "cron-parser"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": "cron-parser", "version": version, "main": "index.js"})
        )
        helper = textwrap.dedent(
            """
            const { DateTime } = require('luxon');
            const at = (ms, zone) => {
              const d = DateTime.fromMillis(ms, { zone });
              return {
                toDate: () => d.toJSDate(), getFullYear: () => d.year, getMonth: () => d.month - 1,
                getDate: () => d.day, getHours: () => d.hour, getMinutes: () => d.minute,
                getSeconds: () => d.second, getMilliseconds: () => d.millisecond,
                getUTCOffset: () => d.offset,
              };
            };
            """
        )
        (package / "index.js").write_text(helper + textwrap.dedent(body))
        if luxon:
            shutil.copytree(luxon_files, root / "node_modules" / "luxon")
        return str(root)

    return make


def synthetic_case(installation, version="5.99.0", **kwargs):
    case = node_case(installation, installation, **kwargs)
    return replace(
        case,
        left=replace(case.left, version=version),
        right=replace(case.right, version=version),
    )


# --- real installations -------------------------------------------------------------------


@LAB
@pytest.mark.parametrize("installation", ["OLD", "NEW"])
def test_identical_installations_match_in_fully_checked_window(installation):
    path = {"OLD": OLD, "NEW": NEW}[installation]
    version = {"OLD": "5.8.1", "NEW": "5.9.0"}[installation]
    case = node_case(path, path)
    case = replace(
        case, left=replace(case.left, version=version), right=replace(case.right, version=version)
    )
    report = compare(case)
    assert report["outcome"] == "MATCH_WITHIN_WINDOW"
    assert report["schema_version"] == 3
    assert report["completeness"]["window_checked"]
    assert report["first_divergence"] is None
    assert report["timezone_data"]["status"] == "same_runtime"
    assert report["profile_differences"]["provenance"] == []
    assert report["profile_differences"]["engine_version_only"] is False


@LAB
def test_upgrade_witness_has_real_different_utc_occurrences():
    case = node_case(OLD, NEW, limits={"context": 10})
    case = replace(
        case,
        left=replace(case.left, version="5.8.1"),
        right=replace(case.right, version="5.9.0"),
    )
    report = compare(case)
    assert report["outcome"] == "DIFFERENT"
    assert report["schema_version"] == 3
    assert report["occurrence_counts"] == {"left": 4, "right": 5}
    witness = report["first_divergence"]
    assert witness["kind"] == "occurrence" and witness["index"] == 2
    # 29 March has a two-hour gap in Troll. 5.8.1 drops the day, 5.9.0 moves it past the gap.
    assert witness["left"]["utc"] == "2026-03-29T23:30:00.000000Z"
    assert witness["left"]["local"] == "2026-03-30T01:30:00.000000+02:00"
    assert witness["right"]["utc"] == "2026-03-29T01:30:00.000000Z"
    assert witness["right"]["local"] == "2026-03-29T03:30:00.000000+02:00"
    assert witness["right"]["offset_seconds"] == 7200
    assert len(utcs(report["context"]["left"])) == 4
    assert len(utcs(report["context"]["right"])) == 5
    differences = report["profile_differences"]
    assert differences["configuration"] == []
    assert differences["provenance"] == ["engine_version"]
    assert differences["engine_version_only"] is True
    resolved = [report["profiles"][side]["resolved"] for side in ("left", "right")]
    assert [item["actual_version"] for item in resolved] == ["5.8.1", "5.9.0"]
    assert resolved[0]["runtime"] == resolved[1]["runtime"]
    assert resolved[0]["loaded"]["luxon"]["version"] == resolved[1]["loaded"]["luxon"]["version"]
    json.dumps(report, allow_nan=False)


@LAB
def test_controls_outside_the_transition_match():
    for kwargs in (
        {"start": "2026-03-23T00:00:00Z", "end": "2026-03-27T00:00:00Z"},
        {"timezone": "UTC"},
        {
            "timezone": "America/New_York",
            "expression": "30 2 * * *",
            "start": "2026-03-07T00:00:00Z",
            "end": "2026-03-11T00:00:00Z",
        },
    ):
        case = node_case(OLD, NEW, **kwargs)
        case = replace(
            case,
            left=replace(case.left, version="5.8.1"),
            right=replace(case.right, version="5.9.0"),
        )
        assert compare(case)["outcome"] == "MATCH_WITHIN_WINDOW", kwargs


@LAB
@pytest.mark.parametrize(
    "start,end,expected",
    [
        # start is inclusive: an occurrence exactly at start is returned, one microsecond
        # later is not, and one microsecond earlier still is.
        ("2026-03-27T01:30:00Z", "2026-03-28T01:30:00Z", ["2026-03-27T01:30:00.000000Z"]),
        (
            "2026-03-27T01:30:00.000001Z",
            "2026-03-28T01:30:00.000001Z",
            ["2026-03-28T01:30:00.000000Z"],
        ),
        ("2026-03-27T01:29:59.999999Z", "2026-03-27T01:30:00Z", []),
        # end is exclusive.
        (
            "2026-03-27T01:29:59.999999Z",
            "2026-03-27T01:30:00.000001Z",
            ["2026-03-27T01:30:00.000000Z"],
        ),
        ("2026-03-27T01:30:00Z", "2026-03-27T01:30:00.000001Z", ["2026-03-27T01:30:00.000000Z"]),
        # a window without any occurrence is fully checked and empty
        ("2026-03-27T01:30:01Z", "2026-03-28T01:29:59Z", []),
        ("2026-03-27T00:00:00Z", "2026-03-27T00:00:01Z", []),
    ],
)
def test_exact_start_and_end_bounds(start, end, expected):
    case = node_case(timezone="UTC", start=start, end=end)
    assert utcs(events(case)) == expected
    report = compare(case)
    assert report["outcome"] == "MATCH_WITHIN_WINDOW"
    assert report["empty_window"] is (not expected)


@LAB
def test_consecutive_occurrences_and_start_on_the_first_of_them():
    case = node_case(
        timezone="UTC",
        expression="*/15 1 * * *",
        start="2026-03-27T01:00:00Z",
        end="2026-03-27T02:00:00Z",
    )
    assert utcs(events(case)) == [
        f"2026-03-27T01:{minute}:00.000000Z" for minute in ("00", "15", "30", "45")
    ]
    # starting exactly on an occurrence and one minute later differ by exactly that occurrence
    later = replace(case, start=case.start.replace(minute=1))
    assert utcs(events(later)) == utcs(events(case))[1:]


@LAB
def test_dst_gap_and_fold_keep_distinct_utc_instants():
    gap = node_case(
        timezone="America/New_York",
        expression="30 2 * * *",
        start="2026-03-07T00:00:00Z",
        end="2026-03-10T00:00:00Z",
    )
    items = events(gap)
    assert utcs(items) == [
        "2026-03-07T07:30:00.000000Z",
        "2026-03-08T07:30:00.000000Z",
        "2026-03-09T06:30:00.000000Z",
    ]
    # the nonexistent 02:30 is reported by the engine as 03:30 after the gap
    assert items[1]["local"] == "2026-03-08T03:30:00.000000-04:00"
    fold = node_case(
        timezone="America/New_York",
        expression="30 * * * *",
        start="2026-11-01T04:00:00Z",
        end="2026-11-01T08:00:00Z",
    )
    first, second = [item for item in events(fold) if "01:30:00" in item["local"]]
    assert first["utc"] == "2026-11-01T05:30:00.000000Z"
    assert second["utc"] == "2026-11-01T06:30:00.000000Z"
    assert first["local"][:23] == second["local"][:23]
    assert (first["offset_seconds"], first["fold"]) == (-14400, 0)
    assert (second["offset_seconds"], second["fold"], second["roundtrip_fold"]) == (-18000, 1, 1)
    assert not second["nonexistent_local"]


@LAB
def test_rejected_expression_and_unknown_timezones():
    rejected = node_case(expression="61 * * * *")
    stream = run_adapter(rejected, rejected.left)
    assert stream.status == "rejected" and not stream.accepted
    assert compare(rejected)["outcome"] == "INVALID_INPUT"
    for timezone in ("Mars/Base", "system", "local", "+0300", "utc+3"):
        case = node_case(timezone=timezone)
        stream = run_adapter(case, case.left)
        assert stream.status == "invalid_timezone", timezone
        assert stream.profile is None
        assert compare(case)["outcome"] == "INVALID_INPUT"


@LAB
def test_missing_package_and_wrong_expected_version(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    for installation in (str(empty), str(tmp_path / "does-not-exist")):
        case = node_case(installation, installation)
        stream = run_adapter(case, case.left)
        assert stream.status == "missing_dependency"
        assert compare(case)["outcome"] == "ENGINE_ERROR"
    # NEW is 5.9.0; asking for another version ends before any occurrence is computed
    case = node_case(NEW, NEW)
    case = replace(case, left=replace(case.left, version="5.8.1"))
    report = compare(case)
    assert report["outcome"] == "UNRESOLVED"
    assert report["termination_reason"]["left"] == "version_mismatch"
    assert report["profiles"]["left"]["resolved"]["actual_version"] == "5.9.0"
    assert report["occurrence_counts"] == {"left": None, "right": 5}
    assert not report["completeness"]["window_checked"]


@LAB
def test_library_comes_only_from_the_selected_installation(tmp_path, synthetic, monkeypatch):
    # Both real installations load their own cron-parser and Luxon.
    for path, version in ((OLD, "5.8.1"), (NEW, "5.9.0")):
        case = node_case(path, path)
        case = replace(case, left=replace(case.left, version=version))
        stream = run_adapter(case, case.left)
        root = str(Path(path).resolve())
        assert stream.profile["installation_realpath"] == root
        for module in stream.profile["loaded"].values():
            assert module["path"].startswith(root + os.sep)
        assert stream.profile["loaded"]["cron-parser"]["version"] == version
    # Decoys in a parent directory, NODE_PATH and a global-looking folder are never used.
    decoy = synthetic("exports.CronExpressionParser = {};", version="9.9.9", name="outer")
    decoys = Path(decoy) / "node_modules"
    inner = Path(decoy) / "inner"
    inner.mkdir()
    monkeypatch.setenv("NODE_PATH", str(decoys))
    case = synthetic_case(str(inner), version="9.9.9")
    stream = run_adapter(case, case.left)
    assert stream.status == "missing_dependency"
    assert "cron-parser" in stream.detail
    # An installation with its own cron-parser but whose Luxon is only in a parent folder.
    shared = Path(synthetic("exports.CronExpressionParser = {};", name="shared"))
    child = shared / "child"
    (child / "node_modules").mkdir(parents=True)
    shutil.move(shared / "node_modules" / "cron-parser", child / "node_modules" / "cron-parser")
    case = synthetic_case(str(child))
    stream = run_adapter(case, case.left)
    assert stream.status == "missing_dependency"
    assert "luxon" in stream.detail


@LAB
def test_node_options_cannot_change_the_loaded_runtime(monkeypatch, tmp_path):
    preload = tmp_path / "preload.js"
    preload.write_text("process.stderr.write('PRELOADED\\n'); process.exit(9);")
    monkeypatch.setenv("NODE_OPTIONS", f"--require {preload}")
    case = node_case()
    stream = run_adapter(case, case.left)
    assert stream.complete and "PRELOADED" not in stream.stderr


@LAB
def test_occurrence_cap_never_matches():
    case = node_case(
        timezone="UTC",
        expression="* * * * *",
        start="2026-03-27T00:00:00Z",
        end="2026-03-28T00:00:00Z",
        limits={"max_occurrences": 5},
    )
    stream = run_adapter(case, case.left)
    assert stream.status == "occurrence_cap" and len(stream.events) == 5
    report = compare(case)
    assert report["outcome"] == "UNRESOLVED"
    assert not report["completeness"]["window_checked"]
    assert report["occurrence_counts"] == {"left": None, "right": None}
    # reaching the cap exactly at the end of the window is complete: one lookup proves it
    exact = replace(
        case, end=case.start.replace(minute=5), limits=replace(case.limits, max_occurrences=5)
    )
    assert run_adapter(exact, exact.left).complete


# --- engine failures (synthetic cron-parser inside a real installation) ----------------------

SEQUENCE = """
exports.CronExpressionParser = {
  parse(expression, options) {
    if (expression === '1 1 1 1 *') throw new Error('Invalid cron expression');
    if (expression === '2 1 1 1 *') throw new TypeError('defect, not a rejection');
    let ms = options.currentDate.getTime();
    let calls = 0;
    return {
      next() {
        calls += 1;
        %s
      },
    };
  },
};
"""


def sequence(step):
    return SEQUENCE % step


@LAB
def test_synthetic_engine_matches_real_semantics(synthetic):
    # Control: a well-behaved fake yields every minute once, proving the transport itself.
    installation = synthetic(sequence("ms += 60000; return at(ms, options.tz);"))
    case = synthetic_case(installation, start="2026-03-27T00:00:00Z", end="2026-03-27T00:03:00Z")
    stream = run_adapter(case, case.left)
    assert stream.complete
    assert utcs(stream.events) == [f"2026-03-27T00:0{m}:00.000000Z" for m in range(3)]


@LAB
@pytest.mark.parametrize(
    "step,status,outcome",
    [
        ("return at(ms, options.tz);", "non_advancing", "ENGINE_ERROR"),
        (
            "ms += calls === 1 ? 120000 : -60000; return at(ms, options.tz);",
            "non_monotonic",
            "ENGINE_ERROR",
        ),
        (
            "throw new Error('Invalid expression, loop limit exceeded');",
            "search_limit",
            "UNRESOLVED",
        ),
        ("throw new Error('Out of the time span range');", "engine_error", "ENGINE_ERROR"),
        ("throw new RangeError('anything else');", "engine_error", "ENGINE_ERROR"),
        ("return { toDate: () => new Date(NaN) };", "engine_error", "ENGINE_ERROR"),
    ],
)
def test_engine_failures_never_prove_a_match(synthetic, step, status, outcome):
    case = synthetic_case(
        synthetic(sequence(step)), start="2026-03-27T00:00:00Z", end="2026-03-27T01:00:00Z"
    )
    stream = run_adapter(case, case.left)
    assert stream.status == status, stream.detail
    assert not stream.complete
    report = compare(case)
    assert report["outcome"] == outcome
    assert report["outcome"] != "MATCH_WITHIN_WINDOW"
    assert not report["completeness"]["window_checked"]


@LAB
def test_parser_failures_are_distinguished(synthetic):
    installation = synthetic(sequence("ms += 60000; return at(ms, options.tz);"))
    plain = synthetic_case(installation, expression="1 1 1 1 *")
    assert run_adapter(plain, plain.left).status == "rejected"
    defect = synthetic_case(installation, expression="2 1 1 1 *")
    stream = run_adapter(defect, defect.left)
    assert stream.status == "engine_error" and "TypeError" in stream.detail
    assert compare(defect)["outcome"] == "ENGINE_ERROR"


@LAB
def test_inconsistent_engine_wall_clock_is_an_engine_error(synthetic):
    installation = synthetic(
        sequence("ms += 60000; const r = at(ms, options.tz); r.getHours = () => 5; return r;")
    )
    case = synthetic_case(installation, timezone="UTC", end="2026-03-27T01:00:00Z")
    stream = run_adapter(case, case.left)
    assert stream.status == "engine_error" and "disagrees" in stream.detail


@LAB
def test_timeout_is_unresolved_and_the_worker_is_gone(synthetic):
    case = synthetic_case(synthetic(sequence("for (;;) {}")), limits={"deadline_seconds": 0.6})
    before = time.monotonic()
    stream = run_adapter(case, case.left)
    assert time.monotonic() - before < 5
    assert stream.status == "timeout" and stream.returncode is not None
    assert not stream.complete
    assert compare_streams(stream, stream)[0] == "UNRESOLVED"


@LAB
def test_prefix_before_timeout_is_kept_but_does_not_complete(synthetic):
    step = "if (calls > 3) { for (;;) {} } ms += 60000; return at(ms, options.tz);"
    # The deadline must outlast Node.js startup on a slow runner, or the prefix is not reached.
    case = synthetic_case(
        synthetic(sequence(step)), limits={"deadline_seconds": 4}, end="2026-03-27T01:00:00Z"
    )
    stream = run_adapter(case, case.left)
    assert stream.status == "timeout" and len(stream.events) == 3
    assert compare(case)["outcome"] == "UNRESOLVED"


@LAB
@pytest.mark.parametrize(
    "script",
    [
        "process.stdout.write('not json\\n')",
        "process.stdout.write('{\"protocol_version\":1')",
        'process.stdout.write(\'{"protocol_version":1,"type":"occurrence"}\\n\')',
        "process.exit(0)",
        "process.stdout.write('x'.repeat(2000000))",
    ],
)
def test_corrupted_worker_output_never_matches(script):
    case = node_case(limits={"stdout_bytes": 1024})
    stream = run_adapter(case, case.left, command=[NODE, "-e", script])
    assert stream.status in {"protocol_error", "output_cap"}
    report = compare(case)
    assert report["outcome"] != "MATCH_WITHIN_WINDOW"
    assert compare_streams(stream, stream)[0] in {"ENGINE_ERROR", "UNRESOLVED"}


@LAB
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group cleanup")
def test_cancellation_terminates_the_node_worker(tmp_path):
    pidfile = tmp_path / "worker.pid"
    script = tmp_path / "cancel.py"
    data = {
        "name": "cancel",
        "node": NODE,
        "timezone": "UTC",
        "start": "2026-03-27T00:00:00Z",
        "end": "2026-03-28T00:00:00Z",
        "limits": {"deadline_seconds": 120},
        "left": {
            "engine": "cron-parser",
            "expression": "* * * * *",
            "installation": NEW,
            "version": "5.9.0",
        },
        "right": {
            "engine": "cron-parser",
            "expression": "* * * * *",
            "installation": NEW,
            "version": "5.9.0",
        },
    }
    # A hanging worker that records its pid; the child process is then interrupted.
    hang = (
        f"require('fs').writeFileSync({str(pidfile)!r}, String(process.pid)); "
        "setInterval(() => {}, 1000)"
    )
    script.write_text(
        textwrap.dedent(
            f"""
            import signal, sys
            # A parent started in the background may have SIGINT ignored; cancellation must work.
            signal.signal(signal.SIGINT, signal.default_int_handler)
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / "src")!r})
            from crondelta.adapters import run_adapter
            from crondelta.config import Case
            case = Case.parse({data!r})
            print("started", flush=True)
            run_adapter(case, case.left, command=[{NODE!r}, "-e", {hang!r}])
            """
        )
    )
    child = subprocess.Popen([sys.executable, "-I", str(script)], stdout=subprocess.PIPE, text=True)

    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    worker = None
    try:
        assert child.stdout.readline().strip() == "started"
        for _ in range(200):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.05)
        worker = int(pidfile.read_text())
        assert alive(worker)
        child.send_signal(signal.SIGINT)
        assert child.wait(timeout=10) != 0
        for _ in range(100):
            if not alive(worker):
                break
            time.sleep(0.05)
        assert not alive(worker)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        if worker is not None and alive(worker):
            os.kill(worker, signal.SIGKILL)


# --- configuration, CLI, and protocol validation (Node.js not required to start) -------------


def base(**overrides):
    side = {
        "engine": "cron-parser",
        "expression": "30 1 * * *",
        "installation": "/somewhere",
        "version": "5.9.0",
    }
    data = {
        "name": "x",
        "node": "node",
        "left": dict(side),
        "right": dict(side),
        "timezone": "UTC",
        "start": "2026-03-27T00:00:00Z",
        "end": "2026-03-28T00:00:00Z",
    }
    return data | overrides


def test_configuration_requires_explicit_installation_version_and_node():
    assert Case.parse(base()).left.is_node
    for mutate in (
        lambda d: d["left"].pop("installation"),
        lambda d: d["right"].pop("version"),
        lambda d: d["left"].update(installation=""),
        lambda d: d["left"].update(python="python3"),
        lambda d: d["left"].update(tzdata_version="2026.2"),
        lambda d: d.pop("node"),
        lambda d: d.update(allow_different_tzdata=True),
        lambda d: d["left"].update(expression="* * * * * *"),
        lambda d: d["left"].update(expression="H * * * *"),
    ):
        data = base()
        mutate(data)
        with pytest.raises((InvalidInput, UnsupportedScope)):
            Case.parse(data)
    with pytest.raises(UnsupportedScope):
        Case.parse(base(left={**base()["left"], "options": {"strict": True}}))


def test_python_engines_are_unchanged_and_cannot_be_mixed_with_node():
    python = {"engine": "croniter", "expression": "30 1 * * *"}
    data = base(left=python)
    with pytest.raises(UnsupportedScope):
        Case.parse(data)
    with pytest.raises(InvalidInput):
        Case.parse(base(left=python, right=python))  # node given without a cron-parser side
    with pytest.raises(InvalidInput):
        Case.parse(base(left={**python, "installation": "/x"}, right=python, node=None))
    case = Case.parse({k: v for k, v in base(left=python, right=python).items() if k != "node"})
    assert set(case.left.to_dict()) == {
        "engine",
        "expression",
        "options",
        "python",
        "version",
        "tzdata_version",
    }
    report = compare(case)
    assert report["schema_version"] == 1
    assert set(report["profiles"]["left"]["requested"]) == set(case.left.to_dict())
    assert "profile_differences" in report and "timezone_data_only" in report["profile_differences"]


@LAB
def test_cli_json_and_manifest_end_to_end(tmp_path, capsys):
    args = [
        "compare",
        "--node",
        NODE,
        "--expression",
        TROLL["expression"],
        "--timezone",
        TROLL["timezone"],
        "--start",
        TROLL["start"],
        "--end",
        TROLL["end"],
        "--left-engine",
        "cron-parser",
        "--left-installation",
        OLD,
        "--left-version",
        "5.8.1",
        "--right-engine",
        "cron-parser",
        "--right-installation",
        NEW,
        "--right-version",
        "5.9.0",
        "--format",
        "json",
    ]
    assert main(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "DIFFERENT" and report["schema_version"] == 3
    assert main(args[:-1] + ["human"]) == 1
    text = capsys.readouterr().out
    assert "Node.js" in text and "Luxon 3.7.2" in text and "5.8.1" in text
    manifest = tmp_path / "manifest.json"
    data = base(node=NODE)
    data["left"] |= {"installation": OLD, "version": "5.8.1"}
    data["right"] |= {"installation": NEW}
    data |= {"timezone": TROLL["timezone"], "start": TROLL["start"], "end": TROLL["end"]}
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [data]}))
    assert main(["compare", "--manifest", str(manifest), "--format", "json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 3 and result["outcome"] == "DIFFERENT"
    # without a Node.js executable and with a Python engine on one side
    assert main([a for a in args if a not in ("--node", NODE)]) == 2
    capsys.readouterr()
    mixed = args[:]
    mixed[mixed.index("--left-engine") + 1] = "croniter"
    for flag in ("--left-installation", "--left-version"):
        del mixed[mixed.index(flag) : mixed.index(flag) + 2]
    assert main(mixed) == 3


def node_header(case, side="left", **overrides):
    profile = getattr(case, side)
    resolved = {
        **profile.to_dict(),
        "requested_version": profile.version,
        "actual_version": "5.9.0",
        "interpreter": "/node",
        "runtime": {
            "implementation": "Node.js",
            "version": "26.0.0",
            "versions": {"node": "26.0.0", "icu": "78.3", "tz": "2026e"},
            "platform": "linux",
            "arch": "x64",
            "executable": "/node",
            "executable_realpath": "/node",
            "icu_environment": [],
        },
        "installation_realpath": "/inst",
        "loaded": {
            "cron-parser": {"version": "5.9.0", "path": "/inst/node_modules/cron-parser/i.js"},
            "luxon": {"version": "3.7.2", "path": "/inst/node_modules/luxon/l.js"},
        },
        "timezone": {
            "provider": "node-intl",
            "source": "Intl.DateTimeFormat (ICU) through Luxon",
            "key": case.timezone,
            "resolved_key": case.timezone,
            "luxon_zone_name": case.timezone,
            "luxon_zone_type": "fixed",
            "icu_version": "78.3",
            "tz_data_version": "2026e",
            "cldr_version": "48",
            "unicode_version": "17.0",
            "tzdata_package_version": None,
            "tzif_sha256": None,
            "unavailable": ["tzdata_package_version", "tzif_sha256"],
        },
    }
    for path, value in overrides.items():
        target = resolved
        *parents, last = path.split(".")
        for part in parents:
            target = target[part]
        target[last] = value
    return record("profile", profile=resolved)


def synthetic_stream(case, header, side="left"):
    messages = [header, record("accepted"), record("end", status="complete")]
    lines = "".join(json.dumps(m) + "\n" for m in messages)
    command = [sys.executable, "-c", f"import sys; sys.stdin.read(); sys.stdout.write({lines!r})"]
    return run_adapter(case, getattr(case, side), command=command)


def test_valid_node_provenance_is_accepted_and_tzdata_claims_are_not_invented():
    case = Case.parse(base())
    stream = synthetic_stream(case, node_header(case))
    assert stream.complete
    assert stream.profile["timezone"]["tzif_sha256"] is None


@pytest.mark.parametrize(
    "path,value",
    [
        ("timezone.tzif_sha256", "a" * 64),
        ("timezone.tzdata_package_version", "2026.2"),
        ("timezone.provider", "tzdata"),
        ("timezone.unavailable", []),
        ("timezone.key", "Europe/Berlin"),
        ("timezone.tz_data_version", None),  # absent value must be listed as unavailable
        ("loaded.luxon.path", "/elsewhere/node_modules/luxon/l.js"),
        ("loaded.cron-parser.version", "5.8.1"),
        ("runtime.versions", {}),
        ("installation", "/changed"),
        ("version", "5.8.1"),
    ],
)
def test_node_provenance_violations_are_protocol_errors(path, value):
    case = Case.parse(base())
    assert synthetic_stream(case, node_header(case, **{path: value})).status == "protocol_error"


def test_python_streams_keep_the_strict_tzdata_guarantee():
    # Node-style timezone provenance must never be accepted for a Python engine.
    case = Case.parse(
        {
            "name": "p",
            "timezone": "UTC",
            "start": "2026-03-27T00:00:00Z",
            "end": "2026-03-28T00:00:00Z",
            "left": {"engine": "croniter", "expression": "30 1 * * *"},
            "right": {"engine": "croniter", "expression": "30 1 * * *"},
        }
    )
    node = Case.parse(base())
    header = node_header(node)
    header["profile"] = {
        **case.left.to_dict(),
        **{k: v for k, v in header["profile"].items() if k in ("runtime", "timezone")},
        "requested_version": None,
        "actual_version": "6.2.4",
        "interpreter": "/python",
    }
    assert synthetic_stream(case, header).status == "protocol_error"


def test_different_runtimes_are_unresolved_and_luxon_changes_are_not_attributed():
    case = Case.parse(base())
    same = synthetic_stream(case, node_header(case))
    other = synthetic_stream(
        case,
        node_header(case, **{"runtime.versions": {"node": "26.0.0", "icu": "79.1", "tz": "2026f"}}),
    )
    outcome, _, reason = compare_streams(same, other)
    assert outcome == "UNRESOLVED" and "different Node.js runtime" in reason
    assert compare_streams(same, same)[0] == "MATCH_WITHIN_WINDOW"
    from crondelta.comparison import report

    luxon = synthetic_stream(case, node_header(case, **{"loaded.luxon.version": "3.8.0"}))
    versions = synthetic_stream(
        case,
        node_header(case, **{"actual_version": "5.8.1", "loaded.cron-parser.version": "5.8.1"}),
    )
    both = synthetic_stream(
        case,
        node_header(
            case,
            **{
                "actual_version": "5.8.1",
                "loaded.cron-parser.version": "5.8.1",
                "loaded.luxon.version": "3.8.0",
            },
        ),
    )
    only_engine = report(case, same, versions)["profile_differences"]
    assert only_engine["provenance"] == ["engine_version"] and only_engine["engine_version_only"]
    mixed = report(case, same, both)["profile_differences"]
    assert mixed["provenance"] == ["engine_version", "luxon_version"]
    assert mixed["engine_version_only"] is False
    assert report(case, same, luxon)["profile_differences"]["provenance"] == ["luxon_version"]
    assert report(case, same, other)["timezone_data"]["status"] == "different"


@LAB
@pytest.mark.skipif(
    not os.environ.get("CRONDELTA_INSTALLED_PYTHON"),
    reason="set after wheel installation; required in CI",
)
def test_installed_wheel_ships_the_node_worker(tmp_path):
    python = os.environ["CRONDELTA_INSTALLED_PYTHON"]
    child = subprocess.run(
        [
            python,
            "-I",
            "-m",
            "crondelta",
            "compare",
            "--format",
            "json",
            "--node",
            NODE,
            "--expression",
            TROLL["expression"],
            "--timezone",
            TROLL["timezone"],
            "--start",
            TROLL["start"],
            "--end",
            TROLL["end"],
            "--left-engine",
            "cron-parser",
            "--left-installation",
            OLD,
            "--left-version",
            "5.8.1",
            "--right-engine",
            "cron-parser",
            "--right-installation",
            NEW,
            "--right-version",
            "5.9.0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 1, child.stderr
    assert json.loads(child.stdout)["outcome"] == "DIFFERENT"


EXPECTED_CASES = {
    "cron_parser_troll_gap": ("DIFFERENT", {"left": 4, "right": 5}),
    "cron_parser_troll_ordinary_control": ("MATCH_WITHIN_WINDOW", {"left": 4, "right": 4}),
    "cron_parser_utc_control": ("MATCH_WITHIN_WINDOW", {"left": 4, "right": 4}),
    "cron_parser_new_york_gap_control": ("MATCH_WITHIN_WINDOW", {"left": 4, "right": 4}),
}
ROOT = Path(__file__).resolve().parents[1]


@LAB
@pytest.mark.parametrize("name", list(EXPECTED_CASES))
def test_historical_upgrade_manifest(name):
    manifest = json.loads((ROOT / "examples/cron-parser-upgrade.json").read_text())
    assert manifest["schema_version"] == 1
    data = next(item for item in manifest["cases"] if item["name"] == name)
    data["node"] = NODE
    for side, path in (("left", OLD), ("right", NEW)):
        data[side]["installation"] = path
    report = compare(Case.parse(data))
    outcome, counts = EXPECTED_CASES[name]
    assert report["outcome"] == outcome and report["schema_version"] == 3
    assert report["occurrence_counts"] == counts
    assert report["completeness"]["window_checked"]
    assert [report["profiles"][s]["resolved"]["actual_version"] for s in ("left", "right")] == [
        "5.8.1",
        "5.9.0",
    ]
    assert report["profile_differences"]["engine_version_only"] is True


def test_saved_cron_parser_results_are_consistent_and_redacted():
    path = ROOT / "examples/reports/cron-parser-upgrade.json"
    text = path.read_text()
    saved = json.loads(text)
    assert saved["report"]["schema_version"] == 3
    assert {case["name"]: case["outcome"] for case in saved["report"]["cases"]} == {
        name: outcome for name, (outcome, _) in EXPECTED_CASES.items()
    }
    for private in ("/Users/", "/home/", "/private/", str(Path.home())):
        assert private not in text
    gap = saved["report"]["cases"][0]
    assert gap["first_divergence"]["index"] == 2
    direct = saved["independent_control"]["cron_parser_troll_gap"]
    assert len(direct["5.8.1"]["occurrences"]) == 4 and len(direct["5.9.0"]["occurrences"]) == 5
    assert direct["5.8.1"]["luxon"] == direct["5.9.0"]["luxon"] == "3.7.2"
    assert gap["timezone_provenance"]["left"]["tzif_sha256"] is None


@LAB
def test_missing_icu_timezone_version_is_listed_as_unavailable(tmp_path):
    # A Node.js build that does not report process.versions.tz must still run and say so.
    preload = tmp_path / "no-tz.cjs"
    preload.write_text(
        "const v = { ...process.versions }; delete v.tz;"
        "Object.defineProperty(process, 'versions', { value: v });"
    )
    worker = Path(__file__).resolve().parents[1] / "src/crondelta/_node_worker.cjs"
    case = node_case()
    stream = run_adapter(case, case.left, command=[NODE, "-r", str(preload), str(worker)])
    assert stream.complete, (stream.status, stream.detail)
    tz = stream.profile["timezone"]
    assert tz["tz_data_version"] is None and tz["icu_version"]
    assert set(tz["unavailable"]) == {"tzdata_package_version", "tzif_sha256", "tz_data_version"}


def test_absent_provenance_value_is_accepted_when_listed():
    case = Case.parse(base())
    header = node_header(
        case,
        **{
            "timezone.tz_data_version": None,
            "timezone.unavailable": ["tzdata_package_version", "tzif_sha256", "tz_data_version"],
        },
    )
    assert synthetic_stream(case, header).complete


@LAB
def test_unresolved_node_report_keeps_the_v3_shape():
    case = node_case(timezone="Mars/Base")
    report = compare(case)
    assert report["schema_version"] == 3 and report["outcome"] == "INVALID_INPUT"
    assert report["timezone_data"] == {
        "different_tzdata_allowed": False,
        "status": "unresolved",
        "versions_equal": None,
        "tzif_equal": None,
        "runtime_equal": None,
    }
    assert report["profile_differences"]["engine_version_only"] is None
    assert "timezone_data_only" not in report["profile_differences"]
