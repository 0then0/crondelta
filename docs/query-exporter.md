# Case study: query-exporter 4.0.2 to 5.0.0

query-exporter's cron library migration provides a concrete example of why
schedule compatibility matters. This case study follows its public API calls
using controlled schedules, fixed times, and explicit timezone data. It checks
trigger calculations without starting the application or executing SQL.

## Documented upstream migration

The project's [changelog](https://github.com/albertodonato/query-exporter/blob/46729cf5ec07c17d94c66845e4ca989345b4a4cb/CHANGES.rst)
documents the switch from croniter to APScheduler in 5.0.0 and warns of cron
format differences.

The source references are pinned to the commits behind these release tags:

- Tag **4.0.2**, commit
  `634cc4e26c6b7c731f88dbb451b62e287ec1f077`.
- Tag **5.0.0**, commit
  `46729cf5ec07c17d94c66845e4ca989345b4a4cb`.

The [4.0.2 executor](https://github.com/albertodonato/query-exporter/blob/634cc4e26c6b7c731f88dbb451b62e287ec1f077/query_exporter/executor.py)
imports croniter and dateutil `gettz`; `_loop_times_iter()` constructs
`croniter(schedule, datetime.now(gettz()))`, calls `next(cron_iter)` for float
Unix seconds, then converts the delay into the event loop's clock. Its
[pyproject](https://github.com/albertodonato/query-exporter/blob/634cc4e26c6b7c731f88dbb451b62e287ec1f077/pyproject.toml)
requires unbounded `croniter` and `python-dateutil`; its
[requirements.txt](https://github.com/albertodonato/query-exporter/blob/634cc4e26c6b7c731f88dbb451b62e287ec1f077/requirements.txt)
pins croniter **6.0.0** and python-dateutil **2.9.0.post0**. The project requires
Python 3.11+.

The [5.0.0 executor](https://github.com/albertodonato/query-exporter/blob/46729cf5ec07c17d94c66845e4ca989345b4a4cb/query_exporter/executor.py)
adds scheduled queries to `AsyncIOScheduler` using
`CronTrigger.from_crontab(query.schedule)`. It supplies no explicit timezone
in that call. Its [pyproject](https://github.com/albertodonato/query-exporter/blob/46729cf5ec07c17d94c66845e4ca989345b4a4cb/pyproject.toml)
requires unbounded `apscheduler`; [uv.lock](https://github.com/albertodonato/query-exporter/blob/46729cf5ec07c17d94c66845e4ca989345b4a4cb/uv.lock)
pins APScheduler **3.11.2**. These are the project's pinned dependency versions;
actual deployments may have installed different versions.

## Reproduced library differences

The reproduction suite uses two version pairs:

- Primary v0.1 baseline: croniter **6.2.4**, APScheduler **3.11.3**.
- Historical pinned control: croniter **6.0.0**, APScheduler **3.11.2**.

Both use tzdata **2026.2**, explicit UTC, and fixed windows. The historical
controls hold tzdb constant; they do not attempt to recreate all historical
dependencies or the host timezone database of a deployed query-exporter.

Controlled schedules, not recovered user configurations:

- Positive control `0 9 1 * mon`, October 2026: croniter OR yields five events;
  APScheduler AND yields zero. First witness is October 1 at 09:00 UTC.
- Positive control `0 9 * * 0`, October 1 through October 8: croniter yields
  October 4, APScheduler October 5, both at 09:00 UTC.
- Negative control `0 9 * * mon`, same week: both yield October 5 at 09:00 UTC.

All these controls produce the same respective verdicts with both version pairs.
The [standalone API probe](../scripts/query_exporter_probe.py) additionally uses
the 4.x `next(cron_iter)` float API and the 5.x actual `CronTrigger` API with
fixed time. The probe injects a dateutil tzfile built from the pinned UTC TZif
where 4.x calls `gettz()`, and supplies explicit UTC to the 5.x constructor.
At these exact modern UTC minutes, the historical float API gives the same UTC
events as CronDelta's datetime API. The float probe is evidence about the
application's call shape; CronDelta's comparator uses exact datetimes.

## Reproducing the results

Run from the repository root after `uv sync --locked`. The baseline controls
and synthetic examples write separate evidence collections:

```sh
uv run python scripts/reproduce.py --output examples/reports/current.json
uv run python scripts/reproduce.py --manifest examples/synthetic.json \
  --output examples/reports/synthetic.json
```

Prepare the historical dependencies explicitly, then run the same controls
and the application API probes:

```sh
uv venv --python 3.13 .venv-historical
uv pip install --python .venv-historical/bin/python \
  croniter==6.0.0 APScheduler==3.11.2 tzdata==2026.2
uv run python scripts/reproduce.py \
  --left-python .venv-historical/bin/python \
  --right-python .venv-historical/bin/python \
  --left-version 6.0.0 --right-version 3.11.2 \
  --output examples/reports/historical.json
.venv-historical/bin/python -I scripts/query_exporter_probe.py \
  --engine croniter > examples/reports/historical-croniter-api.json
.venv-historical/bin/python -I scripts/query_exporter_probe.py \
  --engine apscheduler > examples/reports/historical-apscheduler-api.json
```

For different library versions on the two sides, prepare two environments and
pass their respective interpreter paths. Representative reports intentionally
retain the actual interpreter/runtime and timezone provenance of the run;
absolute paths will differ on another machine.

## Synthetic examples and application boundaries

[synthetic.json](../examples/synthetic.json) separately contains Berlin gap/fold,
Lord Howe's half-hour gap, and a croniter `day_or: false` control. query-exporter's
inspected call does not set `day_or: false`. Those are demonstrations of engine
configuration and DST contracts, not claims about upstream application options.
The historical probe is UTC-only because dateutil and zoneinfo timezone objects
can differ at DST boundaries; their complete DST behavior is not equated here.

The controls demonstrate migration risks at the trigger level. They do not
establish a user incident or an upstream bug. Scheduler startup, deployment-local
timezone selection, event-loop clock conversion, misfire/coalescing, concurrency,
query success, and monitoring require application-level validation and are
outside this case study.
