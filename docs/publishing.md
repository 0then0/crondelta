# Publishing to PyPI

CronDelta publishes a wheel and source distribution through GitHub Actions
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
The workflow is [publish.yml](../.github/workflows/publish.yml). It obtains
short-lived credentials using GitHub OIDC; no PyPI API token or password is
stored in the repository.

## One-time setup

Create an environment named **`pypi`** under the repository's
[Settings → Environments](https://github.com/0then0/crondelta/settings/environments).
Configure its deployment rules for release tags such as `v*`. Required reviewers
can be used when publication should need a maintainer's approval.

For a new PyPI project, open your account's
[Publishing settings](https://pypi.org/manage/account/publishing/) and add a
pending GitHub publisher with these exact values:

- **PyPI Project Name:** `crondelta`
- **Owner:** `0then0`
- **Repository name:** `crondelta`
- **Workflow name:** `publish.yml`
- **Environment name:** `pypi`

Workflow name is the filename, including `.yml`, without `.github/workflows/`.
Environment name must match the publishing job and the GitHub environment.
For an existing project, register the same publisher under that project's
Publishing settings instead.

A [pending publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
creates the PyPI project on the first successful upload. Registration alone does
not reserve the package name. The README's PyPI version badge begins reporting
a version after that first upload.

## Check the release workflow

In GitHub Actions, select **Publish to PyPI → Run workflow** on `main`.
This manual run executes the full Python 3.11/3.13/3.14 verification matrix,
checks version consistency, builds both distributions, validates them with
`uv publish --dry-run`, and saves the `distributions` artifact. The publishing
job is skipped for manual runs.

Local checks from the repository root:

```sh
sh scripts/verify.sh
uv run --no-sync python scripts/check_release.py --tag v0.1.0
uv publish --dry-run --trusted-publishing never \
  dist/crondelta-0.1.0-py3-none-any.whl dist/crondelta-0.1.0.tar.gz
```

The dry run does not upload files or request OIDC credentials. The publishing
action also checks package metadata and README rendering before its real upload.

## Publish a version

1. Set the same version in `pyproject.toml` and `src/crondelta/__init__.py`.
   For subsequent releases, update `uv.lock` with `uv lock`, the current-release
   test in `tests/test_release.py`, and version-specific installation examples.
2. Commit and push the release sources, including both workflow files, and
   confirm that CI passes.
3. Create a [GitHub Release](https://github.com/0then0/crondelta/releases/new)
   for tag **`v0.1.0`** at the intended release commit. For later releases, use
   `v` followed by the exact package version. Publish the release; saving a draft
   or pushing a tag alone does not start the PyPI upload.
4. The workflow checks the release commit on Python 3.11, 3.13, and 3.14, then
   builds the wheel and source distribution. A mismatched tag or runtime/package
   version stops the build.
5. Approve the `pypi` environment deployment if its rules require it. The separate
   publishing job downloads the checked artifacts and uploads them using OIDC.
6. Confirm the version and files on [PyPI](https://pypi.org/project/crondelta/).
   Test installation in a fresh environment with
   `uv tool install crondelta==0.1.0` and `crondelta --version`.

Only the publishing job has `id-token: write`. Builds and tests do not receive
publishing credentials. Regular CI pushes and pull requests do not publish.

PyPI versions cannot be overwritten. For a corrected package, increment the
version and publish a new release. If an upload was interrupted, inspect the
files already present on PyPI before retrying; the workflow does not silently
skip existing distributions.
