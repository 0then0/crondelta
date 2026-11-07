# JSON reports and adapter protocol

Use `--format json` to obtain a versioned CLI report. This reference describes
the public report fields and the internal worker transport used to produce
them. For command-line inputs and exit codes, see the [CLI reference](usage.md).

## Report versions

Reports have `schema_version` **1** unless the case sets `allow_different_tzdata`,
in which case it is **2**, or compares `cron-parser` installations, in which case it is
**3** ([Report v3](#report-v3-nodejs)). Version 1 guarantees that `MATCH_WITHIN_WINDOW` and
`DIFFERENT` come from sides with identical timezone data (the same tzdata version
and TZif bytes). Version 2 withdraws that guarantee, so a consumer that relies on it
can reject reports with another version. Fields of versions 1 and 2 are otherwise
identical. Version 3 never carries the guarantee, because a Node.js run has neither a tzdata
package version nor TZif bytes. A manifest report has the highest `schema_version` of its cases.
Manifest input stays at `schema_version: 1`; its new keys are optional.

## Report v1 and v2

Single-case reports contain:

- `schema_version` (integer 1 or 2), `name`, `outcome`, and `reason`.
- `profiles.left/right`: `requested` and `resolved` (null if resolution failed).
- `window`: normalized microsecond UTC `start`/`end`, bounds, and IANA key.
- `completeness.window_checked`: both streams completed enumeration, independent
  of outcome. Each side has `complete`, `accepted`, exact `count` or null,
  `observed_count`, termination reason, and diagnostics.
- `limits`, `termination_reason`, and `occurrence_counts` by side.
- `empty_window`: true only if both streams completed with no occurrences.
- `first_divergence`: null, a parser acceptance witness, or an occurrence witness.
  Occurrence witness `index` is zero-based. A null side means proven absence of
  further events in a fully completed stream, not missing computation.
- `context.left/right`: indexed evidence around the witness, or the first
  bounded sample when no occurrence witness exists.
- `timezone_provenance.left/right` and `adapter_diagnostics.left/right`.
  Provenance contains `provider` (`tzdata`), `source` (`tzdata.zoneinfo`, the
  package resource; no path), `key`, `tzdata_version` (resolved),
  `requested_tzdata_version` (or null), `tzif_sha256` of the TZif bytes the zone was
  built from, and `zoneinfo_tzpath` (always empty). Null when a side has no profile.
- `timezone_data`: `different_tzdata_allowed`; `status` (`identical`, `different`,
  or `unresolved` if a side has no profile); and `versions_equal`/`tzif_equal`
  (null when unresolved).
- `profile_differences`: `configuration` lists differing `engine`, `expression`,
  `options`; `provenance` lists differing `engine_version`, `runtime`,
  `tzdata_version`, `tzif_sha256` (null if a side is unresolved);
  `timezone_data_only` is true only when `configuration` is empty and `provenance`
  contains nothing beyond the two timezone data entries. This describes tracked
  factors only; it does not name a cause for an occurrence difference.

Configuration failures retain the report envelope (`schema_version` 1) with unknown
profiles, window, `timezone_data`, `profile_differences`, and limits null, `window_checked: false`, and no witness. Manifest output
has `schema_version` (see above), aggregated `outcome`, and an ordered `cases` array of
single-case reports. The reproduction helper saves a `schema_version`/`cases`
collection without an aggregate verdict; it is an evidence collection, not CLI output.

## Report v3 (Node.js)

A case whose profiles use `cron-parser` produces `schema_version` **3**. Python profiles and
their reports are unchanged, and a Python stream is still rejected unless it carries the
tzdata provenance above. Version 3 keeps the envelope and field names of versions 1 and 2,
with these differences:

- `profiles.*.requested` contains `engine`, `expression`, `options` (always empty),
  `installation`, `node`, and `version`. `python` and `tzdata_version` do not exist.
  `profiles.*.resolved` adds `installation_realpath`, `loaded` (`cron-parser` and `luxon`,
  each with the version read from the package that was loaded and the real path of the
  loaded entry file, always inside `installation_realpath`), and `runtime`: `implementation`
  (`Node.js`), `version`, the full `process.versions` map as `versions`, `platform`, `arch`,
  `executable`, `executable_realpath`, and `icu_environment` (names, never values, of
  `NODE_ICU_DATA`, `ICU_TIMEZONE_FILES_DIR`, and `TZ` when set).
- `timezone_provenance.*` has `provider` (`node-intl`), `source`, `key`, `resolved_key` (the
  canonical key reported by `Intl`), `luxon_zone_name`, `luxon_zone_type`, and the
  `icu_version`, `tz_data_version` (`process.versions.tz`), `cldr_version`, and
  `unicode_version` that the runtime reports, or null. `tzdata_package_version` and
  `tzif_sha256` are always null, and `unavailable` lists every value that is null, so a
  missing item is explicit rather than inferred. No tzdata wheel and no TZif file are
  claimed for Node.js.
- `timezone_data`: `different_tzdata_allowed` is always false; `status` is `same_runtime`,
  `different`, or `unresolved`; `versions_equal` and `tzif_equal` are null; `runtime_equal`
  is true only when both sides report the same executable real path, `process.versions`,
  platform, architecture, and ICU environment. Different runtimes are `UNRESOLVED` and
  there is no permission flag.
- `profile_differences`: `configuration` lists differing `engine`, `expression`,
  `options`; `provenance` lists `engine_version`, `runtime`, and `luxon_version`
  (null if a side is unresolved); `engine_version_only` is true only when `configuration`
  is empty and `engine_version` is the only entry in `provenance`. It describes tracked
  factors only and does not name a cause; other dependencies are not compared.
  `timezone_data_only` does not exist in version 3. Installation paths are not factors.

Because the zone rules come from the runtime's ICU data, equal streams show equality for
that runtime only. Consumers that rely on the version 1 guarantee must reject version 3.

## Enumeration contract

Only exact aware UTC datetime equality is used in comparison. Input ISO formats
are normalized; lexical datetime string comparison does not determine identity.
Unknown suffixes are never presumed empty. A next occurrence beyond end, or a
public engine result of no future occurrence, completes that side's window.
Croniter's `CroniterBadDateError` always means an unresolved search, including
expressions such as February 31 that may never yield; no custom satisfiability
solver is used. Exhaustion is obtained via `get_next(datetime)`, not `all_next()`
which can silently stop after a configured search limit.

Croniter's exclusive iterator is seeded at the preceding whole UTC minute;
returned events are then filtered against the exact inclusive start. Five-field
schedules have minute resolution. Input microseconds are preserved, and no
arbitrary epsilon is subtracted. APScheduler starts with
`get_next_fire_time(None, start)` in the selected timezone; subsequent calls
use the previous fire time for both arguments. These initial anchors are part
of the comparison contract and can affect results near DST.

Evidence preserves the engine's original local datetime and its UTC round-trip.
For `30 2 * * *` in Berlin's March 2026 gap, the tested baseline returns
`01:00Z` from croniter and `01:30Z` from APScheduler. APScheduler's original
`02:30+01:00` round-trips to `03:30+02:00`, setting `nonexistent_local: true`.
In the autumn fold, both baseline versions retain two distinct UTC instants
for local 02:30. Repeated local times are valid evidence; repeated or decreasing
UTC instants are stream errors and are never sorted or deduplicated.

## Adapter transport v1

The coordinator runs `[profile.python, "-I", absolute_worker_path]` with pipes
and a deadline. The standalone worker is shipped in the wheel. Each target
interpreter needs Python 3.10+, the selected library, its dependencies, and tzdata; it does not
need CronDelta installed in that environment.

A `cron-parser` profile runs `[node, absolute_node_worker_path]` instead, with `NODE_OPTIONS`
and `NODE_PATH` removed from the environment (see [Node.js worker](#nodejs-worker)). The records
and the coordinator's validation are the same, except for the profile and timezone provenance
described in [Report v3](#report-v3-nodejs).

Stdin is one JSON object: `protocol_version: 1`, `profile`, `timezone`, `start`,
`end`, `max_occurrences`. The profile may pin `tzdata_version`. The worker reads at most 64 KiB. The coordinator's
validated profiles are smaller than this bound. Stdout is UTF-8 NDJSON, with
one complete line per record; stderr is diagnostic only.

Each record has integer `protocol_version: 1` and `type`:

1. `profile` contains `profile`, the resolved profile object. Requested fields
   (`engine`, `expression`, `options`, `python`, `version`, `tzdata_version`) are
   preserved. `requested_version`, `actual_version`, actual `interpreter`, `runtime`
   (implementation/version), and `timezone` provenance are added. The worker builds
   the zone with `ZoneInfo.from_file` from the same wheel bytes whose SHA-256 it
   reports, after disabling the zoneinfo search path and clearing its cache.
2. `accepted` confirms successful expression construction through the engine's
   public API. This is needed to distinguish timeout before parsing from acceptance.
3. Zero or more `occurrence` records contain `occurrence`: `local`, `utc`,
   `offset_seconds`, `fold`, `roundtrip_local`, `roundtrip_fold`, `nonexistent_local`.
   UTC events must increase strictly and lie within the comparison window.
4. Exactly one `end` record has `status` and optional bounded `detail`. Normally
   the process exits 0, including expected rejections and search limits.

Engine construction rejection has a profile then `end: rejected`, with no
`accepted` record. Errors before dependency/timezone resolution can omit the
profile. Public constructor parser exceptions are expected rejections; failures
during evaluation or unexpected exception types are engine errors.

Terminal worker statuses are `complete`, `rejected`, `search_limit`,
`occurrence_cap`, `version_mismatch`, `tzdata_mismatch`, `unsupported`, `engine_error`,
`non_advancing`, `non_monotonic`, `invalid_timezone`, `missing_dependency`.
Transport adds `timeout`, `output_cap`, `protocol_error`, or `engine_error`.
A naturally nonzero process exit, even after a successful terminal record,
invalidates the stream. This remains an engine error when a descendant keeps
the pipes open. Termination by the coordinator to enforce a deadline or output
limit preserves that resource-limit diagnosis.

The worker emits `version_mismatch` (engine) or else `tzdata_mismatch` right after
the profile when a pin is unmet, without parsing or enumerating. The coordinator
rejects a worker that continues after an unmet pin, reports either mismatch status
without one, or returns provenance with another `source`, a different
`requested_tzdata_version`, a nonempty search path, or a malformed digest.
An unavailable zone ends with `invalid_timezone` and names the tzdata version.

Transport v1 itself is unchanged: the worker ships inside the same CronDelta
package as the coordinator, so these record fields are additive and no separate
protocol version is needed.

Malformed records, missing terminal records, inconsistent local/UTC evidence,
extra records after termination, occurrence overflow, changed requested profiles,
and invalid provenance are rejected. A timeout/output cap preserves only fully
validated occurrence lines; a partial final line is not evidence. Stream errors
take precedence over a difference; such a stream cannot justify a verdict.

Queue buffering is bounded (32 chunks of at most 4 KiB); stdout/stderr counters
limit protocol buffering. Evidence contexts contain at most `2 * context + 1`
events per side. Full internal streams have at most `max_occurrences` events.
The deadline applies to each adapter, including startup and waiting for exit;
two adapters run sequentially, so a case can take roughly twice that deadline.

## Node.js worker

`src/crondelta/_node_worker.cjs` is shipped in the wheel and is a thin wrapper over
cron-parser 5.x's public API (`CronExpressionParser.parse` and `next()`). It does not parse or
repair schedules and needs only a Node.js that the selected installation supports (cron-parser 5.x
declares 18+) and that installation.

- **Location.** The package is read from `<installation>/node_modules/cron-parser`, and
  Luxon from the folder Node would resolve for it (nested, then installation level). After
  loading, the worker verifies that every loaded Luxon file lies under that Luxon, and that both
  real paths are inside the installation. Anything else ends with `missing_dependency`
  before any occurrence.
- **Resolution order.** `missing_dependency` and `invalid_timezone` can occur before the
  `profile` record; `version_mismatch` and `unsupported` (not cron-parser 5.x) follow it.
- **Timezone.** The key must be accepted by `Intl.DateTimeFormat` and by Luxon and must not
  resolve to an offset zone. `system`, `local`, and offsets such as `+0300` are
  `invalid_timezone`.
- **Start and end.** `currentDate` is exclusive for `next()`, so the worker seeds it at the whole
  UTC minute before `start`, then discards results before the exact `start`
  (microsecond precision) and stops at the first result at or beyond `end`. An occurrence
  at `start` is included; one at `end` is not. After `max_occurrences`, one more `next()` call
  decides between `complete` and `occurrence_cap`.
- **Errors.** A plain `Error` from `parse()` is `rejected`; other error types are
  `engine_error`. The library's loop-limit error is `search_limit` (an unresolved search);
  every other exception from `next()`, including cron-parser's time span error and invalid
  dates, is `engine_error`. A repeated or decreasing instant is `non_advancing` or
  `non_monotonic`, and nothing is sorted or deduplicated.
- **Evidence.** `local` is the engine result's own wall clock and offset (`CronDate` getters);
  `utc` is `toDate()`. The worker verifies that they agree and otherwise ends with
  `engine_error`. `roundtrip_local` is Luxon's rendering of the UTC instant in the zone, and
  `nonexistent_local` is true if the two wall clocks differ. JavaScript has no `fold`: the flag
  is derived from the instant and the zone offsets only, and is 1 when the same wall-clock
  reading occurred earlier (within the preceding 36 hours) with a larger offset. A repeated
  hour therefore keeps two distinct UTC instants whose `offset_seconds` and `fold` differ.
