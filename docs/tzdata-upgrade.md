# Timezone data upgrade: Havana, tzdata 2026.5 to 2026.6

A schedule can fire at different UTC instants when only the timezone database
changes: the cron engine, its version, the expression, the options, the Python
runtime, and the IANA key all stay the same. CronDelta compares such profiles
only when the case explicitly allows different timezone data
(`--allow-different-tzdata`, or `"allow_different_tzdata": true` in a manifest
case). Without that permission, different timezone data is `UNRESOLVED`.

## Observation

[Apache Airflow PR #76112](https://github.com/apache/airflow/pull/76112) updated
Havana DST test expectations after a tzdata release (2026f) recorded that Cuba
stays on UTC-4 after 2026-11-01. That is external context only: Airflow is not
run here, and the change is a timezone rule update, not a defect in a cron
library or in either tzdata package.

The [manifest](../examples/tzdata-upgrade.json) compares croniter **6.2.4** on both
sides, in one Python runtime, with the same expression `0 9 * * *` and the same
options. Only the tzdata wheel differs: **2026.5** on the left and **2026.6** on
the right. The window is **`[2026-11-02T00:00:00Z, 2026-11-04T00:00:00Z)`**. Local
09:00 is neither repeated nor skipped on either date, so the case does not
depend on how an engine treats an ambiguous or nonexistent local time.

| Case | Outcome | Exit | Left (2026.5) UTC | Right (2026.6) UTC |
| --- | --- | --- | --- | --- |
| `America/Havana` | `DIFFERENT` | 1 | 14:00 on Nov 2 and 3 (`-05:00`) | 13:00 on Nov 2 and 3 (`-04:00`) |
| `Europe/Berlin` control | `MATCH_WITHIN_WINDOW` | 0 | 08:00 on Nov 2 and 3 | 08:00 on Nov 2 and 3 |
| `UTC` control | `MATCH_WITHIN_WINDOW` | 0 | 09:00 on Nov 2 and 3 | 09:00 on Nov 2 and 3 |

For Havana, the first witness at zero-based index 0 is left `14:00Z`, local
`09:00-05:00`, versus right `13:00Z`, local `09:00-04:00`. Both windows are fully
checked (two occurrences each). The only differing profile factors are the
tzdata version and the TZif SHA-256 (`timezone_data_only: true`). In the Berlin and
UTC controls the tzdata *versions* differ but the TZif bytes are identical, so
the report keeps that visible provenance difference while the streams match.

An independent control computes local 09:00 on both dates with the standard
library only (`zoneinfo` over the TZif bytes read from each wheel) and does not
use croniter or CronDelta code. The lab compares its UTC values, local
representations, and TZif hashes to the CLI report.

## Reproduce

All versions and checksums are pinned in `examples/tzdata-lab/`
(`requirements-tzdata-2026.5.txt` and `requirements-tzdata-2026.6.txt`, each with
the `croniter`, `python-dateutil`, `six`, and `tzdata` wheel SHA-256 values).
`prepare` is the only step that installs packages; it creates two disposable
environments in an empty directory and needs `uv` and network access. `run`
installs and downloads nothing.

```sh
lab=$(mktemp -d)
uv run --no-sync python scripts/tzdata_lab.py prepare --workdir "$lab/envs"
uv run --no-sync python scripts/tzdata_lab.py run --workdir "$lab/envs" \
  --output "$lab/tzdata-upgrade.json"
rm -rf "$lab"
```

`run` executes the public CLI (`python -m crondelta compare ...`), once with a
copy of the manifest whose `python` keys point to the lab environments and once
per case with flags, and asserts the table above. The exact commands are in the
saved [results](../examples/reports/tzdata-upgrade.json); the equivalent direct
invocation for Havana is:

```sh
uv run --no-sync crondelta compare \
  --left-engine croniter --left-python "$lab/envs/tzdata-2026.5/bin/python" \
  --left-version 6.2.4 --left-tzdata-version 2026.5 \
  --right-engine croniter --right-python "$lab/envs/tzdata-2026.6/bin/python" \
  --right-version 6.2.4 --right-tzdata-version 2026.6 \
  --allow-different-tzdata --expression '0 9 * * *' --timezone America/Havana \
  --start 2026-11-02T00:00:00Z --end 2026-11-04T00:00:00Z
echo $?   # 1
```

Omitting `--allow-different-tzdata` returns `UNRESOLVED` with exit code 3.
`sh scripts/verify.sh` also prepares the lab and runs it. The saved results
replace the absolute lab and repository paths with `<lab>` and `<repository>`;
the script verifies that no other local path remains. Everything else, including
hashes and runtime versions, is the lab's actual output and can differ by
machine only in the Python runtime.

## What this shows

The comparison establishes which UTC occurrences differ in the checked window
for the two installed timezone databases. It does not establish which release
introduced a rule, whether a deployment's operating system database matches
either wheel, or how a scheduler would treat a changed time. Other installed
packages (for example `python-dateutil`) are not compared.
