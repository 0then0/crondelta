#!/bin/sh
# Run from the repository root. The checkout is mounted read-only and copied.
set -eu
if [ "$#" -eq 0 ]; then
  set -- 3.11 3.14
fi
for runtime in "$@"; do
  docker run --rm -i \
    --mount "type=bind,src=$(pwd),dst=/source,readonly" \
    -e UV_PYTHON="$runtime" -e UV_PYTHON_DOWNLOADS=never \
    "python:$runtime-slim-bookworm" sh <<'CONTAINER'
set -eu
python -m pip install --disable-pip-version-check --quiet uv
mkdir /tmp/crondelta
tar -C /source --exclude=.git --exclude=.venv --exclude=.ruff_cache \
  --exclude=.pytest_cache --exclude=.hypothesis --exclude=build --exclude=dist \
  --exclude='*.egg-info' --exclude=__pycache__ -cf - . | tar -C /tmp/crondelta -xf -
cd /tmp/crondelta
python --version
uv --version
sh scripts/verify.sh
CONTAINER
done
