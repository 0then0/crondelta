# CLI reference

Run `crondelta compare --help` for the available flags. The examples below use
`uv run crondelta` from a checkout; an installed CLI accepts the same arguments.

## Expressions and profiles

Specify both engines, a timezone, and a UTC window. `--expression` supplies a
shared five-field expression. `--left-expression` and `--right-expression`
override it independently. An explicitly empty override is invalid.

```sh
uv run crondelta compare \
  --left-engine croniter --right-engine apscheduler \
  --expression '0 9 1 * mon' --left-options '{"day_or":false}' \
  --timezone UTC --start 2026-10-01T00:00:00Z --end 2026-11-01T00:00:00Z \
  --format json
```

This control matches: both engines use an AND rule and yield no events in the
window. The report has zero counts and `empty_window: true`.

Each side accepts the following profile flags, replacing `left` with `right`
for the other side:

- `--left-engine`: `croniter`, `apscheduler`, `cron-parser`, or `robfig/cron`; required.
  See the profile flags for [cron-parser](#comparing-cron-parser-installations) and
  [robfig/cron](#comparing-with-robfigcron).
- `--left-expression`: independent expression; falls back to `--expression`
  only when omitted. Expressions contain exactly five fields and at most
  1,024 characters.
- `--left-options`: JSON object. Croniter supports `day_or` (boolean, default
  `true`) and `max_years_between_matches` (integer 1..9,999, default `50`).
  APScheduler accepts an empty object; timezone is shared and jitter is excluded.
- `--left-python`: interpreter executable, defaulting to the CLI's interpreter.
  Relative paths resolve from the caller's working directory. Executables are
  passed directly without a shell.
- `--left-version`: exact required installed engine version. A different installed
  version produces `UNRESOLVED`; this flag does not install or select a package.
- `--left-tzdata-version`: exact required version of the `tzdata` package in that
  interpreter. A different installed version produces `UNRESOLVED` before any
  occurrence is computed; nothing is installed or substituted. Optional.

Pass `--allow-different-tzdata` to compare sides that resolve different timezone
data; see [Comparing timezone database versions](#comparing-timezone-database-versions).

Unsupported profile options produce `UNRESOLVED`. A five-field expression
can still be rejected by an engine's parser. Acceptance by one side and rejection
by the other produces `DIFFERENT`; rejection by both produces `INVALID_INPUT`.

## Comparing library versions

Prepare environments explicitly before comparing. Unless you
[allow different timezone data](#comparing-timezone-database-versions), both need the
same tzdata version and the selected engine. For example, to compare croniter versions:

```sh
uv venv --python 3.13 .venv-old
uv pip install --python .venv-old/bin/python croniter==6.0.0 tzdata==2026.2
uv venv --python 3.13 .venv-new
uv pip install --python .venv-new/bin/python croniter==6.2.4 tzdata==2026.2
uv run crondelta compare \
  --left-engine croniter --left-python .venv-old/bin/python --left-version 6.0.0 \
  --right-engine croniter --right-python .venv-new/bin/python --right-version 6.2.4 \
  --expression '0 9 * * mon' --timezone UTC \
  --start 2026-10-01T00:00:00Z --end 2026-10-08T00:00:00Z
```

Target interpreters need Python 3.10+, their engine's dependencies, and tzdata.
The worker is shipped with CronDelta and runs in each target interpreter.
Reports preserve both requested and actual versions, interpreter paths, and
runtime versions. Use trusted executables: worker isolation bounds resources
and is not a sandbox for untrusted Python code.

## Comparing cron-parser installations

To ask whether updating the JavaScript library [`cron-parser`](https://github.com/harrisiirak/cron-parser)
changes when existing schedules fire, select two prepared installations and one Node.js
executable. Node.js, npm, and both installations are external prerequisites that you prepare
yourself (for example with `npm ci`); `compare` never installs, downloads, or creates anything.

```sh
crondelta compare \
  --node /usr/local/bin/node \
  --left-engine cron-parser --left-installation /opt/old --left-version 5.8.1 \
  --right-engine cron-parser --right-installation /opt/new --right-version 5.9.0 \
  --expression '30 1 * * *' --timezone Antarctica/Troll \
  --start 2026-03-27T00:00:00Z --end 2026-03-31T00:00:00Z
```

- `--node` is one executable shared by both sides and is required. Relative names are
  resolved like `--left-python`. `NODE_OPTIONS` and `NODE_PATH` are removed from the worker's
  environment. In a manifest it is the case key `node`.
- `--left-installation` is the directory that contains `node_modules/cron-parser`
  (manifest profile key `installation`); `--left-version` is the exact version you expect
  (key `version`). Both are required for each side. `--left-python`, tzdata flags,
  `--allow-different-tzdata`, and engine options do not apply.
- The library and its Luxon are loaded only from the selected installation. A missing
  package, a missing installation directory, a Luxon found only above the installation (or
  through `NODE_PATH` or a global folder), or a `node_modules/cron-parser` that is a link
  leaving the installation produce a `missing_dependency` error, never a fallback. A different
  installed version is `UNRESOLVED` (`version_mismatch`).
- Expressions have exactly five fields. Each side's expression may be overridden with
  `--left-expression` / `--right-expression`. Windows, limits, and outcomes behave as for the
  Python engines. Comparing cron-parser with croniter or APScheduler is outside this scope
  and is `UNRESOLVED`.

Enumeration uses only `next()`. `currentDate` is exclusive, so the worker anchors one whole UTC
minute before `start` and discards earlier results; no epsilon is subtracted. An occurrence
exactly at `start` is included and one exactly at `end` is excluded. cron-parser has no
end-of-schedule result, so a side is complete only when an occurrence at or beyond `end` is
returned. cron-parser's own loop-limit error is an unresolved `search_limit`; any other exception
is an `engine_error`; a plain `Error` from `parse()` is a rejected expression, whereas other
error types are `engine_error`. Reports use [schema version 3](protocol.md#report-v3-nodejs);
their timezone data is the Node.js runtime's ICU data, not a tzdata package. If the two sides'
Luxon versions differ, the report lists that factor and the difference cannot be attributed to
cron-parser alone. The worker runs JavaScript from the selected installation: use trusted
executables and installations. Limits bound resources and are not a sandbox. The
[upgrade case](cron-parser-upgrade.md) shows a complete example.

## Comparing with robfig/cron

Use `robfig/cron` to compare library-calculated CronJob dates during Kubernetes migration.
Kubernetes v1.34.0 pins `github.com/robfig/cron/v3` at `v3.0.1`. Its controller adds
`TZ=<key>` to a five-field expression with `.spec.timeZone` and calls `cron.ParseStandard`.
[Pinned dependency](https://github.com/kubernetes/kubernetes/blob/v1.34.0/go.mod#L55),
[controller parsing](https://github.com/kubernetes/kubernetes/blob/v1.34.0/pkg/controller/cronjob/cronjob_controllerv2.go#L499-L519),
[timezone formatting](https://github.com/kubernetes/kubernetes/blob/v1.34.0/pkg/controller/cronjob/cronjob_controllerv2.go#L761-L778).

Build the worker from the checkout once with Go 1.19 or newer, then pass its path to `compare`.

```sh
mkdir -p build
(cd workers/robfig-cron && go build -mod=readonly -o ../../build/robfig-cron .)
uv run --no-sync crondelta compare \
  --left-engine apscheduler \
  --right-engine robfig/cron --right-worker ./build/robfig-cron \
  --expression '0 9 1 * MON' --timezone UTC \
  --start 2026-06-01T09:00:00Z --end 2026-07-06T09:00:00Z
```

`robfig/cron` can run on either side, with a Python engine or a second `robfig/cron`
profile. The `--right-worker` path may be absolute or relative to the working
directory and must include a directory component. `--right-version` defaults to
`v3.0.1`, the supported module version. A missing worker produces `ENGINE_ERROR`.
The comparison runs the supplied executable directly, using the existing per-adapter
limits. Building the worker and fetching its Go module are separate preparation steps.

An optional `--right-tzif-path` and `--right-tzif-sha256` pair selects the TZif file
for `--timezone`. Supply the file for that IANA key and its 64-character lowercase
SHA-256. Reports record the digest of the bytes used for enumeration. To hold the zone
data constant against a Python side, use the file from that side's pinned `tzdata` wheel.
Without a file, Go resolves the explicit key through `time.LoadLocation` and may use host
or bundled data. For this lookup the specific database and its digest are unavailable.
The built-in `UTC` source is identifiable. Comparisons can proceed with unknown timezone
provenance, which is shown explicitly in [report v4](protocol.md#report-v4-gorobfigcron).
If both sides report TZif hashes and they differ, use `--allow-different-tzdata` to allow
the comparison. `--right-tzdata-version`, `--right-python`, and engine options do not
apply to `robfig/cron`.

In a manifest, use profile keys `worker`, `version`, `tzif_path`, and `tzif_sha256` for
the equivalent flags. Paths resolve from the working directory for CLI and manifest runs.
The [example manifest](../examples/robfig-cron.json) exercises the day-of-month and
day-of-week rule, DST transitions, and a UTC control. The
[sample report](../examples/reports/robfig-cron.json) includes the preparation and verification
commands.

The result covers the dates returned by the library's parser and scheduler for the
requested window. This profile covers five-field expressions and an explicit IANA key.
It excludes descriptors such as `@every`, embedded `TZ` and `CRON_TZ`, interaction with
a cluster, import of Kubernetes manifests, missed starts, and Job concurrency policy.

## Comparing timezone database versions

By default, two Python sides that resolve different timezone data (a different `tzdata`
version or different TZif bytes for the key) are `UNRESOLVED`; the reason names the
flag below. To ask what changes when only the timezone database changes, prepare one
interpreter per tzdata version, pin each side, and allow the comparison explicitly:

```sh
crondelta compare \
  --left-engine croniter --left-python .venv-tz-old/bin/python \
  --left-version 6.2.4 --left-tzdata-version 2026.5 \
  --right-engine croniter --right-python .venv-tz-new/bin/python \
  --right-version 6.2.4 --right-tzdata-version 2026.6 \
  --allow-different-tzdata --expression '0 9 * * *' --timezone America/Havana \
  --start 2026-11-02T00:00:00Z --end 2026-11-04T00:00:00Z
```

In a manifest case, set `"allow_different_tzdata": true` and, per profile,
`"tzdata_version"`. The [Havana lab](tzdata-upgrade.md) shows a complete example.

With permission, the existing algorithm compares the real UTC streams:

- Different tzdata versions, or different TZif hashes, are not a difference by
  themselves. Equal fully checked streams are `MATCH_WITHIN_WINDOW`, and the
  differing provenance stays in the report.
- Limits, deadlines, completeness, and the `[start, end)` rules are unchanged;
  an incomplete window never yields `MATCH_WITHIN_WINDOW`.
- A requested `--*-tzdata-version` that is not the installed version is
  `UNRESOLVED` (`tzdata_mismatch`), checked by the worker. Pins are optional;
  unpinned sides report the version they happened to resolve.
- A timezone key that exists in one database but not the other is `UNRESOLVED`,
  naming the side and tzdata version; no zone is substituted. Without permission,
  or when no side has the key, it is `INVALID_INPUT`.
- `profile_differences` lists differing `configuration` (engine, expression,
  options) and `provenance` (engine version, runtime, tzdata version, TZif hash).
  `timezone_data_only` is true only if timezone data is the sole tracked difference.
  When other factors differ, an occurrence difference is not attributed to
  timezone data. Interpreter paths and version pins are not counted as factors, and
  dependencies such as `python-dateutil` are not compared.

The Python worker always builds the zone from the TZif bytes of the tzdata wheel that it
hashes, not from the ZoneInfo cache, `PYTHONTZPATH`, the host's `TZ`, or a system
database. `compare` never installs, creates environments, or downloads data.

## Time windows and timezones

`--start` and `--end` must be UTC ISO 8601 timestamps with seconds, using `Z`
or `+00:00`, with at most six fractional digits. Start is inclusive, end is
exclusive, and start must precede end. Supported window years are 0002..9998.

`--timezone` is an explicit IANA key such as `UTC`, `Europe/Berlin`, or
`Australia/Lord_Howe`. Python workers disable system timezone lookup and load the
tzdata wheel. A comparison between Python engines requires the same tzdata version
and TZif bytes on both sides by default. A missing wheel is an engine error. An
unavailable timezone key is invalid input by default.

## Manifests

A manifest is a UTF-8 JSON object containing `schema_version: 1` and 1..100
uniquely named cases. Its maximum size is **1 MiB of actual input bytes**,
including whitespace. Unknown keys are rejected.

```json
{
  "schema_version": 1,
  "cases": [
    {
      "name": "weekday migration",
      "left": {
        "engine": "croniter",
        "expression": "0 9 * * 0",
        "version": "6.2.4"
      },
      "right": {
        "engine": "apscheduler",
        "expression": "0 9 * * 0",
        "version": "3.11.3"
      },
      "timezone": "UTC",
      "start": "2026-10-01T00:00:00Z",
      "end": "2026-10-08T00:00:00Z",
      "limits": {"deadline_seconds": 10, "max_occurrences": 10000}
    }
  ]
}
```

Run it with `crondelta compare --manifest path/to/manifest.json --format json`.
`name`, `left`, `right`, `timezone`, `start`, and `end` are required per case.
Python profile keys are `engine`, `expression`, and optional `options`, `python`,
`version`, `tzdata_version`. See the [cron-parser](#comparing-cron-parser-installations)
and [robfig/cron](#comparing-with-robfigcron) sections for their profile keys.
`limits` and the boolean `allow_different_tzdata`
(default `false`) are optional per case. Manifests written for 0.1 remain valid. Case names contain 1..256 characters.
The entire manifest is validated before any engine starts. All valid cases
run in order. Manifest mode accepts `--format` but cannot be combined with
profile, window, or limit flags.

## Limits

Limits apply per adapter. CLI flags use hyphens; manifest keys use underscores:

- `--deadline-seconds`: default `10`; finite number greater than 0 and at most
  3,600, including startup, input transfer, enumeration, and waiting for exit.
- `--max-occurrences`: default `10000`; integer 1..1,000,000.
- `--stdout-bytes`: default `8388608` (8 MiB); integer 256..67,108,864.
- `--stderr-bytes`: default `65536` (64 KiB); integer 1..1,048,576.
- `--context`: default `2`; integer 0..10. Each side's report includes at most
  `2 * context + 1` occurrences around a difference or as a bounded sample.

Adapters run sequentially, so a case may take roughly twice its adapter deadline.
After reaching the occurrence limit, one extra next-event lookup determines
whether the window ends there. The deadline also bounds that lookup. An engine
search limit cannot prove the absence of future events.

## Exit codes and result handling

- **0 `MATCH_WITHIN_WINDOW`**: both complete UTC streams match in `[start, end)`.
- **1 `DIFFERENT`**: proven occurrence or parser acceptance difference.
- **2 `INVALID_INPUT`**: invalid configuration, unavailable timezone key, or
  rejection by both parsers.
- **3 `UNRESOLVED`**: unsupported scope, engine/tzdata version mismatch, different
  timezone data that was not explicitly allowed, a zone missing from only one allowed
  database, or incomplete computation without a proven difference.
- **4 `ENGINE_ERROR`**: unexpected engine failure, invalid adapter protocol,
  repeated UTC instant, or backward UTC movement.

Manifest exit codes follow this precedence: `ENGINE_ERROR`, `INVALID_INPUT`,
`UNRESOLVED`, `DIFFERENT`, `MATCH_WITHIN_WINDOW`. Read individual case outcomes
from the report as well as the overall result.

Completeness is independent of outcome. `DIFFERENT` may be proven from checked
prefixes before a resource limit. Incomplete sides have `count: null` and an
`observed_count`; their unknown suffixes are never treated as empty. Engine or
protocol errors invalidate a stream even when its prefix appears different.

For `UNRESOLVED`, inspect `termination_reason` and diagnostics. Check requested
versions, `timezone_data`, and timezone provenance first; adjust limits only when the documented
bound is the reason. Increasing a deadline does not turn an engine search limit
into proof that no occurrence exists.

See the [JSON report reference](protocol.md) for the complete field definitions.
