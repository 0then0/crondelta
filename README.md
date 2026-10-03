<p><img src="docs/assets/crondelta.svg" width="96" height="96" alt="CronDelta: two schedules with a differing occurrence inside a clock"></p>

# CronDelta

[![CI](https://img.shields.io/github/actions/workflow/status/0then0/crondelta/ci.yml?branch=main&style=flat)](https://github.com/0then0/crondelta/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat)](pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-64748b?style=flat)](LICENSE)

**Compare actual cron engines before changing the library behind your schedules.**

Changing a cron library, its version, or its options can change when a schedule
fires. CronDelta calls two installed Python engines and compares their UTC
occurrences in a finite window. It reports the first proven difference, the
versions and timezone data used, and whether the window was fully checked.

## Quick start

Use Python 3.11+ and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv sync --locked
uv run crondelta compare \
  --left-engine croniter --left-version 6.2.4 \
  --right-engine apscheduler --right-version 3.11.3 \
  --expression '0 9 1 * mon' --timezone UTC \
  --start 2026-10-01T00:00:00Z --end 2026-11-01T00:00:00Z
```

This returns **`DIFFERENT`**, with exit code **1**. Croniter's default
day-of-month/day-of-week OR rule yields October 1, 5, 12, 19, and 26 at 09:00 UTC.
APScheduler's AND rule yields no occurrences in that window. The first witness
is October 1 at 09:00 UTC versus a fully checked empty right-hand stream.

Changing the expression to `0 9 * * mon` returns **`MATCH_WITHIN_WINDOW`**, with
exit code **0**. This means the streams match throughout the checked
**`[start, end)`** window; it makes no claim about dates outside that window.
A difference can reflect an intentional library contract.

For JSON output, add `--format json`. For multiple schedules:

```sh
uv run crondelta compare --manifest examples/migration.json --format json
```

Install the CLI for use outside the checkout:

```sh
uv build
uv tool install ./dist/crondelta-0.1.0-py3-none-any.whl
crondelta --version
```

Installation prepares dependencies. Running `compare` uses already available
interpreters and libraries; it never installs packages, creates environments,
executes jobs, or connects to a database.

## Supported comparisons

- **Engines:** croniter and APScheduler 3.x `CronTrigger`, using their public APIs.
  The locked baseline is croniter **6.2.4**, APScheduler **3.11.3**, and tzdata
  **2026.2**. Historical UTC controls cover croniter **6.0.0** and APScheduler
  **3.11.2**. Other installed versions can be requested explicitly.
- **Profiles:** independent expressions, supported options, exact version
  requirements, and Python interpreters. This also supports comparing two
  versions of the same engine. Each target interpreter needs its engine and
  tzdata installed; it does not need a separate CronDelta installation.
- **Expressions:** five fields: minute, hour, day-of-month, month, day-of-week.
  Six/seven fields, aliases, random/hash fields, and APScheduler 4.x are outside
  v0.1 scope. Each engine parses its own expression.
- **Timezones:** an explicit IANA key, with both engines forced to use the
  tzdata wheel. Reports record its version and the timezone file's SHA-256.
  Comparing different timezone databases is outside v0.1 scope.
- **Evidence:** exact UTC datetimes, original local time, offset, fold, and UTC
  round-trip. Deadlines and output/occurrence limits bound enumeration. An
  incomplete calculation cannot produce `MATCH_WITHIN_WINDOW`.

CronDelta checks trigger calculations. Scheduler execution policies such as
misfires, coalescing, jitter, and catch-up are outside its scope. It does not
convert expressions or repair migrations automatically.

## Documentation

- [CLI reference](docs/usage.md): profiles, manifests, limits, exit codes, and
  interpreting incomplete results.
- [Report and adapter protocol](docs/protocol.md): JSON fields, evidence, and
  the enumeration contract, including start boundaries and DST.
- [query-exporter migration case study](docs/query-exporter.md): pinned upstream
  sources, controlled differences, and reproduction commands.
- [Testing and support](docs/validation.md): verification commands, CI matrix,
  and tested behavior.
- [Example manifests](examples/) and [representative JSON reports](examples/reports/).

## Related tools

[cron-utils](https://github.com/jmrozanec/cron-utils) provides Java parsing,
validation, dialect mapping, and execution-time calculations.
[cronkit](https://github.com/hzerrad/cronkit) provides cron humanization,
inventories, auditing, timelines, and crontab diffs.
[cron-comparison](https://github.com/Hexagon/cron-comparison) compares and
benchmarks JavaScript cron implementations against fixtures.
CronDelta focuses on two installed Python profiles and reproducible evidence
for a specific time window.

## Development

```sh
sh scripts/verify.sh
```

This runs linting, tests, wheel/sdist builds, installed-package checks, and
reproduction controls. See [Testing and support](docs/validation.md) for details.

Licensed under [Apache-2.0](LICENSE).
