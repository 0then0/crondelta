# Security policy

## Supported releases

Security fixes target the latest published CronDelta release. Older releases do
not have a separate maintenance branch. Check [GitHub Releases](https://github.com/0then0/crondelta/releases)
or [PyPI](https://pypi.org/project/crondelta/) for the current release.

## Report a vulnerability

Email the maintainer privately at [zheltov_pe@mail.ru](mailto:zheltov_pe@mail.ru)
with the subject `CronDelta security report`. Do not open a public issue or pull
request containing exploit details before the report has been assessed.

Include the affected CronDelta/Python/engine versions, operating system, a minimal
reproduction, expected and actual behavior, and the potential impact. Use
sanitized input and test data; do not send credentials or production data.

Reports are handled on a best-effort basis. There is no guaranteed response or
fix deadline. The maintainer will coordinate a fix and public disclosure with
the reporter where possible. For ordinary bugs and intentional engine
differences, use the [issue tracker](https://github.com/0then0/crondelta/issues).

## Trust boundaries

CronDelta invokes the Python executables selected by a profile and imports their
installed libraries. Those executables and packages must be trusted. A manifest
can select an interpreter, so review manifests obtained from other people before
running them.

Worker isolation and resource limits bound schedule enumeration; they do not
sandbox arbitrary Python code. CronDelta does not execute scheduled jobs or SQL.
Published evidence can include local interpreter paths and diagnostics; inspect
it for private information before sharing it.
