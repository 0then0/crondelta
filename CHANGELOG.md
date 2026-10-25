# Changelog

Changes are recorded by release. `Unreleased` describes changes on `main` that
are not part of a published package.

## [Unreleased]

### Added

- A reproducible croniter 6.0.0 to 6.2.4 upgrade case for Zurich's repeated DST
  hour, with a UTC control, representative report, and regression tests.
- Contribution, conduct, and security guidance; issue forms and a pull request
  template; consistent editor and Git settings.
- Monthly grouped Dependabot checks for uv and GitHub Actions, with a seven-day
  cooldown and manual handling of major upgrades.

### Changed

- Routine uv dependency updates now stay within declared version constraints;
  changing the engine/timezone baseline requires a separate review.
- CI cancels superseded runs and bounds verification/build/publishing job time.

### Fixed

- Restored tzdata 2026.2 after an automated update made the baseline and historical
  environments use different timezone provenance, causing verification failures.

## [0.1.0] - 2026-10-04

### Added

- Comparison of real croniter and APScheduler 3.x profiles through independent
  Python interpreters in a finite UTC window.
- Human-readable and versioned JSON reports with first divergence, completeness,
  bounded context, and timezone provenance, including DST gap/fold evidence.
- Named-case manifests, stable exit codes, and execution/output/occurrence limits.
- Controlled query-exporter migration examples and installed-package checks.
- Python 3.11/3.13/3.14 CI and a PyPI Trusted Publishing workflow.

[Unreleased]: https://github.com/0then0/crondelta/compare/v0.1.0...main
[0.1.0]: https://github.com/0then0/crondelta/releases/tag/v0.1.0
