<p><img src="https://raw.githubusercontent.com/0then0/crondelta/refs/heads/main/docs/assets/crondelta.svg" width="96" height="96" alt="CronDelta: two schedules with a differing occurrence inside a clock"></p>

# CronDelta

[![CI](https://img.shields.io/github/actions/workflow/status/0then0/crondelta/ci.yml?branch=main&style=flat)](https://github.com/0then0/crondelta/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/crondelta?style=flat)](https://pypi.org/project/crondelta/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat)](https://github.com/0then0/crondelta/blob/main/pyproject.toml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-64748b?style=flat)](https://github.com/0then0/crondelta/blob/main/LICENSE)

**Compare actual cron engines before changing the library behind your schedules.**

Changing a cron library, its version, or its options can change when a schedule
fires. CronDelta compares UTC occurrences from installed Python engines, two
`cron-parser` installations, or a prebuilt Go worker for `robfig/cron`. It reports the first proven difference, the
versions and timezone data used, and whether the window was fully checked. It can
also show which UTC occurrences change when only the timezone database changes.

## Quick start

Use Python 3.10 to 3.15 and [uv](https://docs.astral.sh/uv/). Install the released CLI
from PyPI. If the release is not yet available there, use the source installation
below.

```sh
uv tool install crondelta==0.3.0
crondelta compare \
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

For JSON output, add `--format json`. For multiple schedules, download the
[migration manifest](https://raw.githubusercontent.com/0then0/crondelta/refs/heads/main/examples/migration.json)
and save it as `migration.json` in your current directory, then run:

```sh
crondelta compare --manifest migration.json --format json
```

The installed CLI does not include example manifests. More
[example manifests](https://github.com/0then0/crondelta/tree/main/examples/) are in
the repository. To build and install from source instead:

```sh
git clone https://github.com/0then0/crondelta.git
cd crondelta
uv build
uv tool install ./dist/crondelta-0.3.0-py3-none-any.whl
crondelta --version
```

Installation prepares dependencies. Running `compare` uses already available
interpreters and libraries; it never installs packages, creates environments,
executes jobs, or connects to a database.

## Supported comparisons

- **Engines:** croniter, APScheduler 3.x `CronTrigger`, `robfig/cron` v3.0.1, and two
  installations of `cron-parser` 5.x. Each adapter uses the engine's public API.
  The locked baseline is croniter **6.2.4**, APScheduler **3.11.3**, and tzdata
  **2026.2**. Historical UTC controls cover croniter **6.0.0** and APScheduler
  **3.11.2**. Other installed versions can be requested explicitly.
- **Profiles:** independent expressions, supported options, exact version
  requirements, and Python interpreters. This also supports comparing two
  versions of the same engine. Each target interpreter needs its engine and
  tzdata installed; it does not need a separate CronDelta installation.
- **Expressions:** five fields: minute, hour, day-of-month, month, day-of-week.
  Six/seven fields, aliases, random/hash fields, and APScheduler 4.x are outside
  the supported scope. Each engine parses its own expression.
- **Timezones:** an explicit IANA key. Python engines use the
  tzdata wheel. Reports record the requested and resolved tzdata version and the
  SHA-256 of the exact TZif file used. Sides with different timezone data are
  `UNRESOLVED` unless the comparison is explicitly allowed with
  `--allow-different-tzdata`; see the
  [tzdata upgrade case](https://github.com/0then0/crondelta/blob/main/docs/tzdata-upgrade.md).
- **cron-parser:** compare two explicitly selected installations with one shared Node.js
  executable, for example `5.8.1` against `5.9.0`. Node.js, npm, and the installations are
  external prerequisites; nothing is installed, and Python engines are not compared with it.
  Reports (schema version 3) record the loaded cron-parser and Luxon versions and the
  Node.js, ICU, and timezone data versions; Node.js has no tzdata package or TZif file.
  See the [cron-parser upgrade case](https://github.com/0then0/crondelta/blob/main/docs/cron-parser-upgrade.md).
- **robfig/cron:** compare the Kubernetes CronJob schedule calculation with APScheduler or
  another Python profile. Supply the compiled worker by path. Report schema 4 records
  the loaded module and Go runtime, the IANA key, and timezone provenance where available.
- **Evidence:** exact UTC datetimes, original local time, offset, fold, and UTC
  round-trip. Deadlines and output/occurrence limits bound enumeration. An
  incomplete calculation cannot produce `MATCH_WITHIN_WINDOW`.

CronDelta checks trigger calculations. Scheduler execution policies such as
misfires, coalescing, jitter, and catch-up are outside its scope. It does not
convert expressions or repair migrations automatically.

## Kubernetes CronJob schedule comparison

The worker uses `ParseStandard` and `Schedule.Next` from `robfig/cron/v3` **v3.0.1**.
Kubernetes **v1.34.0** pins that dependency and passes a valid `.spec.timeZone` to
`ParseStandard` as `TZ=<IANA key>` before the five cron fields. The result covers
dates calculated by the library. Cluster access, manifest import, descriptors such as
`@every`, expression-level `TZ` and `CRON_TZ`, missed runs, and Job concurrency policies
are outside this comparison. [Kubernetes dependency](https://github.com/kubernetes/kubernetes/blob/v1.34.0/go.mod#L55),
[CronJob controller](https://github.com/kubernetes/kubernetes/blob/v1.34.0/pkg/controller/cronjob/cronjob_controllerv2.go#L499-L519),
[time zone handling](https://github.com/kubernetes/kubernetes/blob/v1.34.0/pkg/controller/cronjob/cronjob_controllerv2.go#L761-L778),
[robfig/cron v3.0.1 scheduler](https://github.com/robfig/cron/blob/v3.0.1/spec.go#L56-L93).

From the checkout, with the locked Python environment and Go 1.19 or later:

```sh
lab=$(mktemp -d)
uv run --no-sync python examples/robfig-cron-lab/check.py prepare --workdir "$lab"
(cd workers/robfig-cron && go build -mod=readonly -o "$lab/robfig-worker" .)
uv run --no-sync crondelta compare \
  --left-engine apscheduler --right-engine robfig/cron --right-worker "$lab/robfig-worker" \
  --expression '0 9 1 * MON' --timezone UTC \
  --start 2026-06-01T09:00:00Z --end 2026-07-06T09:00:00Z
```

Both engines include June 1 at 09:00 UTC. `robfig/cron` also includes June 8, 15,
22, 29, and July 1 at 09:00 UTC because it uses OR for restricted day-of-month and
day-of-week fields. APScheduler requires both fields to match. The command exits 1
for `DIFFERENT`.

The prepared manifest repeats that check and compares `30 2 * * *` around both DST
transitions in `Europe/Berlin`, with a UTC control. It pins each zone's TZif bytes
from `tzdata==2026.2` by SHA-256. `robfig/cron` skips March 29 at 02:30 local.
APScheduler emits 01:30 UTC, which converts back to 03:30 local. Both engines return
00:30 and 01:30 UTC for the repeated 02:30 on October 25, and the UTC control matches.

```sh
(cd workers/robfig-cron && go build -mod=readonly -o "$lab/robfig-direct" \
  ../../examples/robfig-cron-lab/direct.go)
uv run --no-sync crondelta compare --manifest "$lab/manifest.json" --format json \
  > "$lab/report.json"  # exit 1
uv run --no-sync python examples/robfig-cron-lab/check.py verify --workdir "$lab" "$lab/report.json"
```

`compare` starts the supplied binary without building it or downloading modules.
Without a TZif pin, Go resolves the zone normally and the report marks unavailable
source and hash metadata. The saved example includes the complete UTC streams and
reproduction commands. [Example report](examples/reports/robfig-cron.json).

## Documentation

- [CLI reference](https://github.com/0then0/crondelta/blob/main/docs/usage.md): profiles, manifests, limits, exit codes, and
  interpreting incomplete results.
- [Report and adapter protocol](https://github.com/0then0/crondelta/blob/main/docs/protocol.md): JSON fields, evidence, and
  the enumeration contract, including start boundaries and DST.
- [query-exporter migration case study](https://github.com/0then0/crondelta/blob/main/docs/query-exporter.md): pinned upstream
  sources, controlled differences, and reproduction commands.
- [Croniter upgrade case](https://github.com/0then0/crondelta/blob/main/docs/croniter-upgrade.md): croniter 6.0.0 to 6.2.4
  across Zurich's repeated DST hour, with a UTC control.
- [Timezone data upgrade case](https://github.com/0then0/crondelta/blob/main/docs/tzdata-upgrade.md): the same croniter
  6.2.4 and expression under tzdata 2026.5 and 2026.6 in Havana, with Berlin and UTC
  controls and an independent TZif check.
- [cron-parser upgrade case](https://github.com/0then0/crondelta/blob/main/docs/cron-parser-upgrade.md): cron-parser
  5.8.1 to 5.9.0 across Antarctica/Troll's two-hour DST gap, with UTC and ordinary controls
  and a direct public API check.
- [Testing and support](https://github.com/0then0/crondelta/blob/main/docs/validation.md): verification commands, CI matrix,
  and tested behavior.
- [Example manifests](https://github.com/0then0/crondelta/tree/main/examples/) and [representative JSON reports](https://github.com/0then0/crondelta/tree/main/examples/reports/).

## Related tools

[cron-utils](https://github.com/jmrozanec/cron-utils) provides Java parsing,
validation, dialect mapping, and execution-time calculations.
[cronkit](https://github.com/hzerrad/cronkit) provides cron humanization,
inventories, auditing, timelines, and crontab diffs.
[cron-comparison](https://github.com/Hexagon/cron-comparison) compares and
benchmarks JavaScript cron implementations against fixtures.
CronDelta focuses on two installed Python profiles or cron-parser installations and reproducible evidence
for a specific time window.

## Contributing

See [CONTRIBUTING.md](https://github.com/0then0/crondelta/blob/main/CONTRIBUTING.md)
for setup, validation, and reproducible evidence requirements. Participation
follows the [Code of Conduct](https://github.com/0then0/crondelta/blob/main/CODE_OF_CONDUCT.md).
Report vulnerabilities privately as described in
[SECURITY.md](https://github.com/0then0/crondelta/blob/main/SECURITY.md).
See [CHANGELOG.md](https://github.com/0then0/crondelta/blob/main/CHANGELOG.md) for
released and unreleased changes.

## Development

From a checkout:

```sh
uv sync --locked
uv run crondelta --version
sh scripts/verify.sh
```

This runs linting, tests, wheel/sdist builds, installed-package checks, and
reproduction controls. See [Testing and support](https://github.com/0then0/crondelta/blob/main/docs/validation.md) for details.

Licensed under [Apache-2.0](https://github.com/0then0/crondelta/blob/main/LICENSE).
