# Contributing to CronDelta

Use issues for reproducible bugs, documentation improvements, and focused feature
proposals. For security concerns, follow [SECURITY.md](SECURITY.md). Participation
is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

Use Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```sh
git clone https://github.com/0then0/crondelta.git
cd crondelta
uv sync --locked
uv run --no-sync crondelta --version
```

Keep `uv.lock` committed. Do not update dependencies as a side effect of an
unrelated change. The project uses argparse, subprocess isolation, and actual
engine APIs; discuss new dependencies, adapters, or changes to the documented
scope before implementing them.

## Making a change

- Keep each pull request focused on one problem and explain the resulting behavior.
- Use Python 3.11-compatible code. Ruff defines Python formatting and lint rules;
  `.editorconfig` defines whitespace conventions for other files.
- Add tests for meaningful behavior changes and credible regressions. A
  documentation-only change does not need an application test.
- Update the relevant documentation and the `Unreleased` section of
  [CHANGELOG.md](CHANGELOG.md) for user-visible changes.
- Use English Conventional Commit messages such as `fix: preserve start inclusion`,
  `test: cover a repeated hour`, or `docs: clarify manifest setup`.

## Validation

Start with the affected tests and static checks:

```sh
uv run --no-sync pytest -q tests/test_cli.py tests/test_transport.py
uv run --no-sync ruff check src tests scripts
uv run --no-sync ruff format --check src tests scripts
git diff --check
```

Choose the test files relevant to your change. Before submitting changes to
engine behavior, transport, packaging, or verification, run:

```sh
sh scripts/verify.sh
```

This runs the full suite, prepares disposable installed/historical environments,
builds wheel and source distributions, and executes the reproduction controls.
A plain pytest run skips tests that require the environment variables prepared
by that helper. See [Testing and support](docs/validation.md) for the CI matrix,
Linux container checks, and support boundaries.

For documentation and configuration, check links, syntax, and examples as
applicable. List the commands actually run and any checks that could not run in
the pull request; do not claim unexecuted checks passed.

## Reproducible schedule evidence

The baseline pins croniter 6.2.4, APScheduler 3.11.3, and tzdata 2026.2. Ordinary
Dependabot updates for uv are limited to the lockfile within declared constraints.
A baseline change requires reviewing the pinned tests, historical environment,
examples, and documentation together.

Use prepared interpreters and the existing adapters. Both sides must use the
same timezone provenance. Generate representative reports through the CLI or
reproduction helper; never edit reported occurrences or runtime provenance to
match an expectation. Explain the chosen window, completeness, and limits.
See the [croniter upgrade example](docs/croniter-upgrade.md) for a complete case.

Do not run production jobs or SQL to demonstrate trigger calculations. Remove
secrets and private data from reports submitted publicly. Interpreter paths can
contain personal information; describe any redaction explicitly.

The project is distributed under the [Apache-2.0 license](LICENSE).
