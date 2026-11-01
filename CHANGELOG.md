# Changelog

Changes are recorded by release. `Unreleased` describes changes on `main` that
are not part of a published package.

## [Unreleased]

## [0.2.0] - 2026-11-02

### Added

- Explicit comparison across timezone database versions: `--allow-different-tzdata`
  (manifest `allow_different_tzdata`) and per-side `--left-tzdata-version` /
  `--right-tzdata-version` (manifest profile `tzdata_version`). The worker verifies the
  installed tzdata version and ends with `tzdata_mismatch` without computing anything
  when a pin is unmet. `compare` still installs and downloads nothing.
- Reports add `timezone_data` and `profile_differences`, list differing configuration
  and provenance factors, and record `requested_tzdata_version` and the data `source`
  for each side. Reports of cases that allow different tzdata have `schema_version` 2.
- A hash-pinned, reproducible Havana lab (tzdata 2026.5 and 2026.6, Berlin and UTC
  controls, independent stdlib TZif control) driven through the public CLI:
  `scripts/tzdata_lab.py`, `docs/tzdata-upgrade.md`, and a representative result.
- A reproducible croniter 6.0.0 to 6.2.4 upgrade case for Zurich's repeated DST
  hour, with a UTC control, representative report, and regression tests.
- Contribution, conduct, and security guidance; issue forms and a pull request
  template; consistent editor and Git settings.
- Monthly grouped Dependabot checks for uv and GitHub Actions, with a seven-day
  cooldown and manual handling of major upgrades.

### Changed

- Different timezone data remains `UNRESOLVED` by default, now with a reason that names
  both tzdata versions, the TZif comparison, and the flag that allows the comparison.
- The worker builds each zone from the exact TZif bytes it hashes
  (`ZoneInfo.from_file`) rather than reading the file and constructing the zone
  separately; the transport validates the new provenance fields.
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

[Unreleased]: https://github.com/0then0/crondelta/compare/v0.2.0...main
[0.2.0]: https://github.com/0then0/crondelta/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/0then0/crondelta/releases/tag/v0.1.0
