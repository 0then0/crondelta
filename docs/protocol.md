# JSON reports and adapter protocol

Use `--format json` to obtain a versioned CLI report. This reference describes
the public report fields and the internal worker transport used to produce
them. For command-line inputs and exit codes, see the [CLI reference](usage.md).

## Report v1

Single-case reports contain:

- `schema_version` (integer 1), `name`, `outcome`, and `reason`.
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

Configuration failures retain the report envelope with unknown profiles,
window, and limits null, `window_checked: false`, and no witness. Manifest output
has `schema_version: 1`, aggregated `outcome`, and an ordered `cases` array of
single-case reports. The reproduction helper saves a `schema_version`/`cases`
collection without an aggregate verdict; it is an evidence collection, not CLI output.

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
interpreter needs Python 3.11+, the selected library, its dependencies, and tzdata; it does not
need CronDelta installed in that environment.

Stdin is one JSON object: `protocol_version: 1`, `profile`, `timezone`, `start`,
`end`, `max_occurrences`. The worker reads at most 64 KiB. The coordinator's
validated profiles are smaller than this bound. Stdout is UTF-8 NDJSON, with
one complete line per record; stderr is diagnostic only.

Each record has integer `protocol_version: 1` and `type`:

1. `profile` contains `profile`, the resolved profile object. Requested fields
   (`engine`, `expression`, `options`, `python`, `version`) are preserved.
   `requested_version`, `actual_version`, actual `interpreter`, `runtime`
   (implementation/version), and `timezone` provenance are added.
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
`occurrence_cap`, `version_mismatch`, `unsupported`, `engine_error`,
`non_advancing`, `non_monotonic`, `invalid_timezone`, `missing_dependency`.
Transport adds `timeout`, `output_cap`, `protocol_error`, or `engine_error`.
A naturally nonzero process exit, even after a successful terminal record,
invalidates the stream. This remains an engine error when a descendant keeps
the pipes open. Termination by the coordinator to enforce a deadline or output
limit preserves that resource-limit diagnosis.

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
