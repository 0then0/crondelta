#!/bin/sh
# Disposable package installation is a development check, never part of compare.
set -eu
uv sync --locked
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv run --no-sync python scripts/check_release.py
uv build
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
runtime=$(uv run --no-sync python -c 'import sys; print(sys.executable)')
version=$(uv run --no-sync python -c 'from crondelta import __version__; print(__version__)')
uv run --no-sync python - "$version" <<'PY'
import sys
import tarfile
import zipfile

version = sys.argv[1]
worker = "crondelta/_node_worker.cjs"
with zipfile.ZipFile(f"dist/crondelta-{version}-py3-none-any.whl") as wheel:
    assert worker in wheel.namelist(), "wheel lacks the Node.js worker"
with tarfile.open(f"dist/crondelta-{version}.tar.gz") as sdist:
    assert f"crondelta-{version}/src/{worker}" in sdist.getnames(), "sdist lacks the Node.js worker"
PY
uv venv --python "$runtime" "$scratch/installed"
uv pip install --python "$scratch/installed/bin/python" "dist/crondelta-$version-py3-none-any.whl"
uv venv --python "$runtime" "$scratch/historical"
uv pip install --python "$scratch/historical/bin/python" \
  croniter==6.0.0 APScheduler==3.11.2 tzdata==2026.2
uv run --no-sync python scripts/tzdata_lab.py prepare --workdir "$scratch/tzdata-lab" \
  --python "$runtime"
# Node.js and npm are external prerequisites of the cron-parser comparison. They are optional
# for a local run (those tests are skipped) and required when CRONDELTA_REQUIRE_NODE=1, as in CI.
node_lab=
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  node_lab="$scratch/cron-parser-lab"
  uv run --no-sync python scripts/cron_parser_lab.py prepare --workdir "$node_lab"
  export CRONDELTA_NODE="$(command -v node)"
  export CRONDELTA_CRON_PARSER_OLD="$node_lab/cron-parser-5.8.1"
  export CRONDELTA_CRON_PARSER_NEW="$node_lab/cron-parser-5.9.0"
elif [ "${CRONDELTA_REQUIRE_NODE:-}" = 1 ]; then
  echo "Node.js and npm are required (CRONDELTA_REQUIRE_NODE=1)" >&2
  exit 1
else
  echo "skipping the cron-parser lab: node and npm were not found" >&2
fi
CRONDELTA_INSTALLED_PYTHON="$scratch/installed/bin/python" \
CRONDELTA_HISTORICAL_PYTHON="$scratch/historical/bin/python" \
CRONDELTA_TZDATA_OLD_PYTHON="$scratch/tzdata-lab/tzdata-2026.5/bin/python" \
CRONDELTA_TZDATA_NEW_PYTHON="$scratch/tzdata-lab/tzdata-2026.6/bin/python" \
CRONDELTA_SECOND_PYTHON="${CRONDELTA_SECOND_PYTHON:-$scratch/installed/bin/python}" \
  uv run --no-sync pytest -q
uv run --no-sync python scripts/reproduce.py --output "$scratch/reports.json"
uv run --no-sync python scripts/reproduce.py --manifest examples/synthetic.json \
  --output "$scratch/synthetic.json"
uv run --no-sync python scripts/reproduce.py \
  --left-python "$scratch/historical/bin/python" \
  --right-python "$scratch/historical/bin/python" \
  --left-version 6.0.0 --right-version 3.11.2 --output "$scratch/historical.json"
uv run --no-sync python scripts/reproduce.py --manifest examples/croniter-upgrade.json \
  --left-python "$scratch/historical/bin/python" --right-python "$runtime" \
  --left-version 6.0.0 --right-version 6.2.4 --output "$scratch/croniter-upgrade.json"
uv run --no-sync python scripts/tzdata_lab.py run --workdir "$scratch/tzdata-lab" \
  --output "$scratch/tzdata-upgrade.json"
if [ -n "$node_lab" ]; then
  uv run --no-sync python scripts/cron_parser_lab.py run --workdir "$node_lab" \
    --output "$scratch/cron-parser-upgrade.json"
fi
uv run --no-sync python scripts/query_exporter_probe.py --engine croniter > "$scratch/croniter.json"
uv run --no-sync python scripts/query_exporter_probe.py --engine apscheduler > "$scratch/apscheduler.json"
