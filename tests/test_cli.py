import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from crondelta import __version__
from crondelta.cli import main
from crondelta.comparison import compare
from crondelta.config import InvalidInput, Limits, UnsupportedScope, utc_datetime

ARGS = [
    "compare",
    "--left-engine",
    "croniter",
    "--right-engine",
    "apscheduler",
    "--expression",
    "0 9 * * mon",
    "--timezone",
    "UTC",
    "--start",
    "2026-10-01T00:00:00Z",
    "--end",
    "2026-10-08T00:00:00Z",
    "--format",
    "json",
]


def test_cli_json_and_human(capsys):
    assert main(ARGS) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == 1
    assert data["outcome"] == "MATCH_WITHIN_WINDOW"
    assert main(ARGS[:-1] + ["human"]) == 0
    assert "not global equivalence" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args,code,outcome",
    [
        (["--expression", "0 9 * * 0"], 1, "DIFFERENT"),
        (["--expression", "bad"], 2, "INVALID_INPUT"),
        (["--left-options", '{"unsupported":true}'], 3, "UNRESOLVED"),
        (["--left-python", "/missing/python"], 4, "ENGINE_ERROR"),
        (["--left-version", "0.0.0"], 3, "UNRESOLVED"),
        (["--start", "2026-10-01T00:00:00.0000001Z"], 2, "INVALID_INPUT"),
        (["--left-options", '{"day_or":1}'], 2, "INVALID_INPUT"),
        (["--deadline-seconds", "nan"], 2, "INVALID_INPUT"),
        (["--unknown"], 2, "INVALID_INPUT"),
        (["--left-options", "[" * 2000 + "]" * 2000], 2, "INVALID_INPUT"),
    ],
)
def test_exit_codes_and_json_errors(capsys, args, code, outcome):
    assert main(ARGS + args) == code
    assert json.loads(capsys.readouterr().out)["outcome"] == outcome


@pytest.fixture
def manifest():
    case = {
        "name": "control",
        "left": {"engine": "croniter", "expression": "0 9 * * mon"},
        "right": {"engine": "apscheduler", "expression": "0 9 * * mon"},
        "timezone": "UTC",
        "start": "2026-10-01T00:00:00Z",
        "end": "2026-10-08T00:00:00Z",
    }
    return {"schema_version": 1, "cases": [case]}


def test_manifest_and_unique_names(tmp_path, capsys, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    args = ["compare", "--manifest", str(path), "--format", "json"]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["cases"][0]["name"] == "control"
    manifest["cases"] *= 2
    path.write_text(json.dumps(manifest))
    assert main(args) == 2
    assert "unique" in json.loads(capsys.readouterr().out)["reason"]


@pytest.mark.parametrize("side", ["left", "right"])
def test_empty_expression_override_is_invalid(side, monkeypatch, capsys):
    def unexpected_compare(case):
        pytest.fail("invalid input must be rejected before starting engines")

    monkeypatch.setattr("crondelta.cli.compare", unexpected_compare)
    assert main(ARGS + [f"--{side}-expression", ""]) == 2
    assert json.loads(capsys.readouterr().out)["outcome"] == "INVALID_INPUT"


def test_explicit_expression_override(capsys):
    assert main(ARGS + ["--right-expression", "0 10 * * mon"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["profiles"]["left"]["requested"]["expression"] == "0 9 * * mon"
    assert report["profiles"]["right"]["requested"]["expression"] == "0 10 * * mon"


@pytest.mark.parametrize("deadline", [10**400, -(10**400)])
def test_huge_manifest_deadline_returns_invalid_input(
    deadline, manifest, tmp_path, monkeypatch, capsys
):
    def unexpected_compare(case):
        pytest.fail("invalid input must be rejected before starting engines")

    monkeypatch.setattr("crondelta.cli.compare", unexpected_compare)
    manifest["cases"][0]["limits"] = {"deadline_seconds": deadline}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert main(["compare", "--manifest", str(path), "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["outcome"] == "INVALID_INPUT"
    assert not captured.err


@pytest.mark.parametrize("deadline", [float("inf"), float("nan"), 0, 3601, True])
def test_invalid_deadline(deadline):
    with pytest.raises(InvalidInput):
        Limits.parse({"deadline_seconds": deadline})


@pytest.mark.parametrize("deadline", [0.01, 3600, 3600.0])
def test_valid_deadline(deadline):
    assert Limits.parse({"deadline_seconds": deadline}).deadline_seconds == deadline


@pytest.mark.parametrize("excess", [0, 1])
def test_manifest_byte_limit(excess, manifest, tmp_path, capsys):
    manifest["cases"][0]["name"] = "контроль"
    payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    path = tmp_path / "manifest.json"
    path.write_bytes(payload + b" " * (1024 * 1024 - len(payload) + excess))
    assert main(["compare", "--manifest", str(path), "--format", "json"]) == 2 * excess
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == ("INVALID_INPUT" if excess else "MATCH_WITHIN_WINDOW")
    if excess:
        assert "1 MiB" in report["reason"]


@pytest.mark.skipif(os.name != "posix", reason="/dev/stdin pipe input")
@pytest.mark.parametrize("padding,code", [(0, 0), (2 * 1024 * 1024, 2)])
def test_manifest_pipe_size_limit(padding, code, manifest):
    payload = json.dumps(manifest).encode() + b" " * padding
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "crondelta",
            "compare",
            "--manifest",
            "/dev/stdin",
            "--format",
            "json",
        ],
        input=payload,
        capture_output=True,
        timeout=10,
    )
    assert child.returncode == code
    report = json.loads(child.stdout)
    assert report["outcome"] == ("INVALID_INPUT" if code else "MATCH_WITHIN_WINDOW")
    if code:
        assert "1 MiB" in report["reason"]
    assert not child.stderr


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-01",
        "2026-10-01T00:00:00",
        "2026-10-01T00:00:00+01:00",
        "2026-02-30T00:00:00Z",
        "2026-10-01T00:00:00.1234567Z",
    ],
)
def test_invalid_timestamps(timestamp):
    with pytest.raises(InvalidInput):
        utc_datetime(timestamp)


@pytest.mark.parametrize(
    "overrides,exception",
    [
        ({"timezone": "../UTC"}, InvalidInput),
        ({"left": {"expression": "* * * * *"}}, InvalidInput),
        ({"start": "2026-10-08T00:00:00Z"}, InvalidInput),
        ({"limits": {"max_occurrences": 0}}, InvalidInput),
        ({"left": {"engine": "croniter", "expression": "H * * * *"}}, UnsupportedScope),
        (
            {
                "left": {
                    "engine": "apscheduler",
                    "expression": "* * * * *",
                    "options": {"jitter": 1},
                }
            },
            UnsupportedScope,
        ),
    ],
)
def test_configuration_validation(make_case, overrides, exception):
    with pytest.raises(exception):
        make_case(**overrides)


def test_two_explicit_interpreters(make_case, tmp_path):
    # A second actual interpreter process, with a separate venv executable. CI
    # can point this to a different Python version/dependency environment.
    target = os.environ.get("CRONDELTA_SECOND_PYTHON")
    if target is None:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "venv",
                "--without-pip",
                "--system-site-packages",
                str(tmp_path / "venv"),
            ],
            check=True,
        )
        target = str(tmp_path / "venv" / "bin" / "python")
        # Nested venvs inherit the base installation, not the parent's site-packages.
        # Reuse the already installed distributions without invoking a package manager.
        site_dir = subprocess.check_output(
            [target, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            text=True,
        ).strip()
        import croniter

        Path(site_dir, "crondelta-test.pth").write_text(
            str(Path(croniter.__file__).parent.parent) + "\n"
        )
    case = make_case()
    case = replace(
        case,
        left=replace(case.left, python=str(Path(sys.executable).absolute())),
        right=replace(case.right, python=target),
    )
    result = compare(case)
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    assert (
        result["profiles"]["left"]["resolved"]["interpreter"]
        != result["profiles"]["right"]["resolved"]["interpreter"]
    )


@pytest.mark.skipif(
    not os.environ.get("CRONDELTA_INSTALLED_PYTHON"),
    reason="set after wheel installation; required in CI",
)
def test_installed_package_outside_checkout(tmp_path):
    python = os.environ["CRONDELTA_INSTALLED_PYTHON"]
    child = subprocess.run(
        [python, "-I", "-m", "crondelta", *ARGS],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(child.stdout)
    assert result["outcome"] == "MATCH_WITHIN_WINDOW"
    child = subprocess.run(
        [str(Path(python).with_name("crondelta")), "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == f"CronDelta {__version__}"
