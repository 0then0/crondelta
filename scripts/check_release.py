"""Validate package version consistency and, optionally, the release tag."""

import argparse
import ast
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no TOML parser; read the one needed key.
    tomllib = None


def project_version(text: str) -> str:
    if tomllib is not None:
        return tomllib.loads(text)["project"]["version"]
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    match = re.search(r'^version\s*=\s*"([^"]+)"', table.group(1) if table else "", re.MULTILINE)
    if match is None:
        raise ValueError("pyproject.toml has no [project] version")
    return match.group(1)


def check_release(root: Path, tag: str | None = None) -> str:
    version = project_version((root / "pyproject.toml").read_text(encoding="utf-8"))
    module = ast.parse((root / "src/crondelta/__init__.py").read_text(encoding="utf-8"))
    runtime_version = next(
        (
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
        ),
        None,
    )
    if runtime_version != version:
        raise ValueError(
            f"package version {version!r} differs from __version__ {runtime_version!r}"
        )
    if tag is not None and tag != f"v{version}":
        raise ValueError(f"release tag must be v{version}, got {tag!r}")
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="exact GitHub release tag, for example v0.2.0")
    args = parser.parse_args()
    try:
        version = check_release(Path(__file__).resolve().parent.parent, args.tag)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"CronDelta {version}: release metadata verified")


if __name__ == "__main__":
    main()
