# cron-parser upgrade: Antarctica/Troll's two-hour gap

Before updating `cron-parser`, you can check whether the schedules you already have
will fire at different instants. This example compares two released installations of
the JavaScript library, **5.8.1** and **5.9.0**, with the same Node.js executable, the
same Luxon (**3.7.2**), the same expression, and the same window. The comparison calls
each installation's public `CronExpressionParser.parse(...).next()`.

Neither version is presented as a current defect. The difference described here is a
fixed behavior change between two historical releases.

## Upstream context

- [BullMQ's job scheduler](https://github.com/taskforcesh/bullmq/blob/master/src/classes/job-scheduler.ts)
  uses `cron-parser` when it calculates the next run. This is context only: CronDelta
  does not run BullMQ or any scheduler, and the case is not evidence of adoption or of
  how any BullMQ deployment behaves.
- [cron-parser pull request #435](https://github.com/harrisiirak/cron-parser/pull/435)
  (merge commit `da1d21b1481f13b9c3a707bc6d7d99251327715b`, 2026-08-12) fixes the
  compensation of a DST transition that skips more than one wall-clock hour. Before the
  fix, `next()` dropped a schedule inside such a gap for the whole day. The first
  release that contains the merge commit is **5.9.0** (tag `v5.9.0`, published
  2026-08-13). The previous release, **5.8.1**, does not contain it.
- [Pull request #405](https://github.com/harrisiirak/cron-parser/pull/405) (merge
  commit `e2bac3a04b534792f1616c2e825f3950cb9f429c`, 2026-06-20) fixes iteration that
  stopped after a spring-forward gap. Tag `v5.6.0` points to a commit **before** that
  merge; the first release that contains it is **5.6.1**. The 5.6.0 to 5.6.1 range also
  contains two unrelated changes (#410 and #413), so this case study uses #435 instead.

The pair was chosen from the upstream history (`git tag --contains` for each merge commit),
not from release notes. Only the `next()` path is exercised: the fix is not a `prev()` or
`includesDate()` change.

## Controlled comparison

The [manifest](../examples/cron-parser-upgrade.json) compares `30 1 * * *`. On 2026-03-29
Antarctica/Troll moves from UTC+0 to UTC+2 at 01:00 UTC, so local 01:00 to 03:00 does not
exist. Windows are `[start, end)` in UTC.

| Case | Outcome | Exit | 5.8.1 | 5.9.0 |
| --- | --- | --- | --- | --- |
| `Antarctica/Troll`, 2026-03-27 to 2026-03-31 | `DIFFERENT` | 1 | 4 occurrences; 29 March absent | 5 occurrences; 29 March at `01:30Z` |
| `Antarctica/Troll`, 2026-03-23 to 2026-03-27 (no transition) | `MATCH_WITHIN_WINDOW` | 0 | 4 occurrences | 4 occurrences |
| `UTC`, 2026-03-27 to 2026-03-31 (control) | `MATCH_WITHIN_WINDOW` | 0 | 4 occurrences | 4 occurrences |
| `America/New_York`, `30 2 * * *`, 2026-03-07 to 2026-03-11 (one-hour gap) | `MATCH_WITHIN_WINDOW` | 0 | 4 occurrences | 4 occurrences |

For the Troll gap the first witness is at zero-based index 2: 5.8.1 yields `2026-03-29T23:30:00Z`
(local `2026-03-30T01:30+02:00`), while 5.9.0 yields `2026-03-29T01:30:00Z` (local
`2026-03-29T03:30+02:00`). Both windows are fully checked. The only differing tracked
factor is the cron-parser version (`engine_version_only: true`): the Node.js runtime and
Luxon are the same for both sides. The [saved results](../examples/reports/cron-parser-upgrade.json)
contain the commands, versions, the report, and the independent control below.

The lab also checks the result without CronDelta. A short script loads each installation's
`cron-parser` and calls `CronExpressionParser.parse(expression, { currentDate, tz }).next()`
until the window ends. The lab asserts that these direct occurrences equal the CronDelta
streams for every case and both versions.

One further check was made during development and is not part of the saved reproduction: the
upstream commit before #435 (`b587efa`) behaves like 5.8.1 for this expression, and the merge
commit behaves like 5.9.0. The 5.8.1 to 5.9.0 range also contains #437 and #440, which concern
hashed steps and stringifying ranges and do not apply to this expression.

## Reproduce

Versions are pinned in `examples/cron-parser-lab/<version>/package.json` and
`package-lock.json` (exact `cron-parser` and `luxon 3.7.2`, with registry integrity hashes).
`prepare` is the only step that installs anything: it copies each lockfile into an empty
directory and runs `npm ci --ignore-scripts`. It needs Node.js, npm, and network access.
`run` installs and downloads nothing.

```sh
lab=$(mktemp -d)
uv run --no-sync python scripts/cron_parser_lab.py prepare --workdir "$lab/envs"
uv run --no-sync python scripts/cron_parser_lab.py run --workdir "$lab/envs" \
  --output "$lab/cron-parser-upgrade.json"
rm -rf "$lab"
```

The equivalent direct comparison for the Troll case, after `prepare`:

```sh
uv run --no-sync crondelta compare \
  --node "$(command -v node)" \
  --left-engine cron-parser --left-installation "$lab/envs/cron-parser-5.8.1" \
  --left-version 5.8.1 \
  --right-engine cron-parser --right-installation "$lab/envs/cron-parser-5.9.0" \
  --right-version 5.9.0 \
  --expression '30 1 * * *' --timezone Antarctica/Troll \
  --start 2026-03-27T00:00:00Z --end 2026-03-31T00:00:00Z
echo $?   # 1
```

`sh scripts/verify.sh` prepares the installations and runs the lab when Node.js and npm are
available. The saved results replace the absolute lab, repository, and Node.js paths with
labels; everything else is the real output of the run, including the Node.js, ICU, and
timezone data versions of the machine that produced it.

## What this shows

The comparison establishes which UTC occurrences differ in the checked windows for the two
installations, one Node.js runtime, and one Luxon version. It does not establish that a
deployment uses those versions, which release introduced a rule, how a scheduler such as
BullMQ treats the result, or whether another Node.js build gives the same ICU timezone data.
The zone rules come from the runtime's ICU data (reported as `tz_data_version`), not from a
tzdata package or TZif file, so a different Node.js build can change the result. Other
dependencies are not compared.
