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

- `--left-engine`: `croniter` or `apscheduler`; required.
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

Target interpreters need Python 3.11+, their engine's dependencies, and tzdata.
The worker is shipped with CronDelta and runs in each target interpreter.
Reports preserve both requested and actual versions, interpreter paths, and
runtime versions. Use trusted executables: worker isolation bounds resources
and is not a sandbox for untrusted Python code.

## Comparing timezone database versions

By default, two sides that resolve different timezone data (a different `tzdata`
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

The worker always builds the zone from the TZif bytes of the tzdata wheel that it
hashes, not from the ZoneInfo cache, `PYTHONTZPATH`, the host's `TZ`, or a system
database. `compare` never installs, creates environments, or downloads data.

## Time windows and timezones

`--start` and `--end` must be UTC ISO 8601 timestamps with seconds, using `Z`
or `+00:00`, with at most six fractional digits. Start is inclusive, end is
exclusive, and start must precede end. Supported window years are 0002..9998.

`--timezone` is an explicit IANA key such as `UTC`, `Europe/Berlin`, or
`Australia/Lord_Howe`. Workers disable system timezone lookup and load the
tzdata wheel. Unless different timezone data is explicitly allowed, both sides must
resolve to the same tzdata version and TZif bytes. A missing wheel is an engine
error; an unavailable timezone key is invalid input.

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
Profile keys are `engine`, `expression`, and optional `options`, `python`,
`version`, `tzdata_version`. `limits` and the boolean `allow_different_tzdata`
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
