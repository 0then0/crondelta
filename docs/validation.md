# Testing and support

CronDelta requires Python 3.11+. GitHub Actions runs the full verification suite
on Ubuntu with Python **3.11**, **3.13**, and **3.14**. The same helper can run
locally on Linux and macOS.

## Full verification

From the repository root, with uv installed:

```sh
sh scripts/verify.sh
```

The helper synchronizes the locked development environment, checks Ruff lint
and formatting, builds the wheel and source distribution, and runs pytest with
Hypothesis. It also installs the wheel into a disposable environment and tests
the module and console command outside the checkout.

An additional disposable environment contains historical engines for migration
controls. Both temporary environments are removed when verification exits.
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
for the independent-interpreter test.

## Disposable Linux verification

With Docker running:

```sh
sh scripts/verify_linux.sh 3.11
sh scripts/verify_linux.sh 3.14
```

Without arguments, the helper runs both versions. It uses official Python
Debian Bookworm images, mounts the checkout read-only, copies sources into a
temporary container directory, and runs `verify.sh`. Dependencies are installed
inside the disposable containers.

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

## Support boundaries

Linux and macOS are the verified platforms. Windows descendant cleanup is not
certified; on Windows the coordinator terminates and reaps only the direct
child. Python 3.12 is allowed by package metadata but is outside the CI matrix.

Tests establish behavior for the listed versions and controlled windows. Other
engine versions, different timezone database releases, scheduler execution
policies, and full query-exporter application behavior are not covered by those
results. Consult the [enumeration contract](protocol.md#enumeration-contract)
when investigating a difference near DST or an incomplete search.

CI verifies the package; it has no package publication, release, or tagging step.
