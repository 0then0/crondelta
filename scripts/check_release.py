"""Validate package version consistency and, optionally, the release tag."""

import argparse
import ast
import tomllib
from pathlib import Path


def check_release(root: Path, tag: str | None = None) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
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
    parser.add_argument("--tag", help="exact GitHub release tag, for example v0.1.0")
    args = parser.parse_args()
    try:
        version = check_release(Path(__file__).resolve().parent.parent, args.tag)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"CronDelta {version}: release metadata verified")


if __name__ == "__main__":
    main()
