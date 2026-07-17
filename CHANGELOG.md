# Changelog

All notable changes to this project are documented here.

## [0.1.5] - 2026-07-18

### Added

- Test suite (`pytest`) covering redaction, message truncation, dedup/rate-limit
  state, Telegram delivery, the public API (`init`/`report`/`capture`/
  `ASGIMiddleware`), and the CLI dashboard.
- CI workflow that runs the test suite on push/PR and gates PyPI publishing on
  it passing.
- `ruff` (lint + format) and `mypy` (type checking), enforced in CI and via
  `pre-commit`.
- `CONTRIBUTING.md` and `SECURITY.md`.
- README: a real dashboard screenshot (generated from actual CLI output),
  FAQ, Privacy & Security, and Roadmap sections; Ruff/mypy/Downloads badges.

### Changed

- Internal module state (`_state`) is now a typed `TypedDict` instead of a
  plain dict, and `report()`/`capture()`/the ASGI middleware now print a
  clear "init() was not called" message instead of silently attempting a
  malformed Telegram request when used before `init()`.
- "devalerts vs. a full error tracker" reframed as "Why not Sentry?".

### Fixed

- Tests badge now uses img.shields.io instead of the raw GitHub Actions
  badge URL, which didn't render reliably (notably on PyPI's own README
  rendering, unlike the other shields.io badges).

## [0.1.4] - 2026-07-17

### Added

- PyPI discovery metadata (keywords, classifiers, project URLs).
- MIT license.

### Changed

- Split the single-file implementation into focused modules
  (`_alert`, `_store`, `_telegram`, `cli`).
- Redesigned the CLI dashboard: aligned columns, relative timestamps, color,
  status dots.
- Strengthened the README.
- Dropped the "throwaway prototype" framing now that the package is published.

## [0.1.3] - 2026-07-17

### Added

- Global exception hook (`init`) delivering unhandled exceptions to Telegram.
- Manual reporting via `report()` and `capture()` (context manager/decorator).
- `ASGIMiddleware` for FastAPI/Starlette error reporting.
- Secret redaction for common token/key patterns before sending.
- Alert message formatting with hard truncation to Telegram's 4096-char limit.
- Fingerprint-based dedup, per-group rate limiting, and the `devalerts
  dashboard` CLI command.

### Fixed

- Re-init recursion: calling `init()` twice no longer chains the exception
  hook to itself.

## [0.1.0] - 2026-07-17

### Added

- Initial project scaffold (`uv init --lib`).
