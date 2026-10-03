#!/bin/sh
# Disposable package installation is a development check, never part of compare.
set -eu
uv sync --locked
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
uv build
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
runtime=$(uv run --no-sync python -c 'import sys; print(sys.executable)')
uv venv --python "$runtime" "$scratch/installed"
uv pip install --python "$scratch/installed/bin/python" dist/crondelta-0.1.0-py3-none-any.whl
uv venv --python "$runtime" "$scratch/historical"
uv pip install --python "$scratch/historical/bin/python" \
  croniter==6.0.0 APScheduler==3.11.2 tzdata==2026.2
CRONDELTA_INSTALLED_PYTHON="$scratch/installed/bin/python" \
CRONDELTA_HISTORICAL_PYTHON="$scratch/historical/bin/python" \
CRONDELTA_SECOND_PYTHON="${CRONDELTA_SECOND_PYTHON:-$scratch/installed/bin/python}" \
  uv run --no-sync pytest -q
uv run --no-sync python scripts/reproduce.py --output "$scratch/reports.json"
uv run --no-sync python scripts/reproduce.py --manifest examples/synthetic.json \
  --output "$scratch/synthetic.json"
uv run --no-sync python scripts/reproduce.py \
  --left-python "$scratch/historical/bin/python" \
  --right-python "$scratch/historical/bin/python" \
  --left-version 6.0.0 --right-version 3.11.2 --output "$scratch/historical.json"
uv run --no-sync python scripts/query_exporter_probe.py --engine croniter > "$scratch/croniter.json"
uv run --no-sync python scripts/query_exporter_probe.py --engine apscheduler > "$scratch/apscheduler.json"
