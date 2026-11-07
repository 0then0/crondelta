# Testing and support

CronDelta supports Python **3.10** to **3.15**. GitHub Actions runs the full
verification suite on Ubuntu with every one of those versions (3.10, 3.11, 3.12, 3.13,
3.14, and 3.15) and Node.js 24. The same helper can run locally on Linux and macOS.
Comparing `cron-parser` installations also needs Node.js and npm as external
prerequisites (cron-parser 5.x declares Node.js 18+; the tests were run with Node.js 18, 22, 24, and 26);
the Python package has no Node.js dependency.

## Full verification

From the repository root, with uv installed:

```sh
sh scripts/verify.sh
```

The helper synchronizes the locked development environment, checks Ruff lint
and formatting, validates package version consistency, builds the wheel and
source distribution, and runs pytest with
Hypothesis. It also installs the wheel into a disposable environment and tests
the module and console command outside the checkout.

An additional disposable environment contains historical engines for migration
controls, and two more contain the hash-pinned tzdata 2026.5 and 2026.6 lab
([timezone data upgrade](tzdata-upgrade.md)). When `node` and `npm` are on `PATH`, two
disposable npm installations of `cron-parser` 5.8.1 and 5.9.0 are prepared with `npm ci`
from the committed lockfiles ([cron-parser upgrade](cron-parser-upgrade.md)); without them
those tests are skipped and a message says so, unless `CRONDELTA_REQUIRE_NODE=1`, which CI
sets, turns their absence into a failure. All temporary environments are removed
when verification exits.
Verification installs dependencies for development; `crondelta compare` itself
does not install anything. No jobs or SQL queries are executed.

To run targeted checks in an already prepared development environment:

```sh
uv run --no-sync pytest -q tests/test_cli.py tests/test_transport.py
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
```

The installed-package test requires `CRONDELTA_INSTALLED_PYTHON`; a plain pytest
run skips it when unset. Use `verify.sh` to prepare that interpreter and run the
full suite. `CRONDELTA_HISTORICAL_PYTHON` selects the historical controls, and
`CRONDELTA_SECOND_PYTHON` optionally selects a different prepared interpreter
for the independent-interpreter test. `CRONDELTA_TZDATA_OLD_PYTHON` and
`CRONDELTA_TZDATA_NEW_PYTHON` select the lab interpreters; without them those
tests are skipped. `CRONDELTA_NODE`, `CRONDELTA_CRON_PARSER_OLD`, and
`CRONDELTA_CRON_PARSER_NEW` select the Node.js executable and the prepared 5.8.1 and 5.9.0
installations (`python scripts/cron_parser_lab.py prepare --workdir DIR`, then the
`DIR/cron-parser-<version>` directories); without them the cron-parser integration tests are
skipped. The tzdata unit tests use synthetic databases in temporary
virtual environments, so they need no network and do not depend on the installed
tzdata.

## Disposable Linux verification

With Docker running:

```sh
sh scripts/verify_linux.sh 3.10
sh scripts/verify_linux.sh 3.15
```

Without arguments, the helper runs every supported version (3.10 to 3.15). It uses
official Python Debian Bookworm images, mounts the checkout read-only, copies sources
into a temporary container directory, installs the distribution's `nodejs` and `npm`,
and runs `verify.sh`. Dependencies are installed inside the disposable containers.

## Tested contracts

The locked baseline is croniter **6.2.4**, APScheduler **3.11.3**, and tzdata
**2026.2**. Historical UTC controls use croniter **6.0.0** and APScheduler
**3.11.2**, with the same timezone data.

The suite covers:

- UTC schedules, named/numeric weekdays, DOM/DOW OR versus AND, and independent
  expressions that produce equal streams within a window.
- Exact start inclusion and end exclusion, fractional timestamps, leap years,
  rare schedules, fully checked empty windows, and engine search limits.
- Berlin DST gap/fold and Lord Howe's half-hour transition, preserving local
  time, offset, fold, UTC, and round-trip evidence.
- First divergence and unequal counts after a common prefix. Property tests
  check symmetry, sound witnesses, and absence of false matches for incomplete
  streams; neither engine is a correctness oracle for the other.
- Parser rejection, unexpected engine exceptions, malformed protocol records,
  repeated/backward UTC movement, and version/timezone mismatches.
- Deadline, input backpressure, output/occurrence limits, natural child crashes,
  pipe-holding descendants, and process cleanup.
- CLI/manifest validation, empty expression overrides, very large numeric
  limits, and the actual manifest byte limit for files and pipes.
- Independent interpreters, installed-package invocation, builds, and the
  [migration reproduction controls](query-exporter.md).
- The [croniter upgrade case](croniter-upgrade.md): real 6.0.0/6.2.4 streams
  across Zurich's repeated hour, exact counts and fold evidence, and a UTC control.
- The [tzdata upgrade lab](tzdata-upgrade.md): Havana `DIFFERENT` with the expected
  witness, Berlin and UTC controls, and an independent stdlib TZif control, driven
  through the public CLI.
- Explicit different-tzdata comparison: strict default, equal and different TZif
  bytes across package versions, requested/resolved tzdata checks, zones missing from
  one database, incomplete windows, window boundaries, factor classification,
  worker/transport provenance validation, and isolation from `PYTHONTZPATH`, `TZ`, and
  the ZoneInfo cache.
- [cron-parser comparison](cron-parser-upgrade.md) with a real Node.js and real
  installations: matching identical installations, the 5.8.1 to 5.9.0 witness with real
  UTC occurrences, exact start/end bounds, empty windows and consecutive occurrences,
  DST gap and fold without losing distinct UTC instants, rejected expressions, unknown
  timezones, a missing package, a wrong expected version, library resolution restricted
  to the selected installation (decoys in a parent folder, `NODE_PATH`, and `NODE_OPTIONS`),
  timeout, occurrence cap, corrupted output, non-advancing and non-monotonic engines,
  cancellation (SIGINT) terminating the worker, report v3 provenance validation, and the
  packaged wheel. Failure paths use a minimal synthetic cron-parser inside a real
  installation with the real Luxon; the Python engines and their report formats are
  covered unchanged.
- Release tag/version matching and runtime/package version consistency.

## Support boundaries

Linux and macOS are the verified platforms. Windows descendant cleanup is not
certified; on Windows the coordinator terminates and reaps only the direct
child. This applies to the Node.js worker as well.

Tests establish behavior for the listed versions and controlled windows. Other
engine versions, cron-parser releases other than the pinned lab pair, other Node.js builds (their
ICU timezone data can differ), timezone database releases other than the pinned lab pair, scheduler execution
policies, and full query-exporter application behavior are not covered by those
results. Consult the [enumeration contract](protocol.md#enumeration-contract)
when investigating a difference near DST or an incomplete search.

CI verifies the package on pushes and pull requests. A separate workflow
publishes PyPI distributions after a GitHub Release is published and verified.
