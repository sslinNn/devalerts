# Changelog

All notable changes to this project are documented here.

## [0.2.3] - 2026-07-18

### Fixed

- `devalerts dashboard` no longer crashes with `UnicodeEncodeError` on
  consoles using a legacy codepage (e.g. `cp1251`) when a group's backoff
  multiplier is shown — the `×` suffix (`limited ×2`) now falls back to
  `x2` like the dashboard's other unicode characters already did.

### Added

- README: demo GIF showing setup, the dashboard, `mute`, and a real alert
  landing in Telegram with the collapsed traceback.

## [0.2.2] - 2026-07-18

### Added

- `capture()` used as a decorator now tags the alert with the wrapped
  function's `__qualname__` as `job` automatically — no need to pass
  `extra={"job": ...}` by hand. Explicit `extra` still overrides it on a key
  collision. A bare `with capture():` block has no function to name, so this
  only applies to decorator usage.

## [0.2.1] - 2026-07-18

### Changed

- Messages now send with `parse_mode: "HTML"` and fold the traceback into a
  collapsed `<blockquote expandable>` — the exception type/message/host are
  visible immediately, the (often huge) traceback expands on tap instead of
  dumping a wall of text into the chat. The 4096-char budget is computed
  against the plain text (what Telegram actually counts), then HTML-escaped
  and wrapped.

## [0.2.0] - 2026-07-18

### Added

- `devalerts.init_celery()`: reports Celery task failures automatically via
  the `task_failure` signal. `init()`'s excepthook alone never sees
  exceptions raised inside a task, since Celery catches those itself to
  record the task's `FAILURE` state — same class of problem `ASGIMiddleware`
  solves for ASGI request errors. Tags each alert with the task name and id.
  Celery is imported lazily, not a devalerts dependency.
- Every alert now includes the sending host (`socket.gethostname()`) — useful
  when one bot serves multiple processes/servers. `init(tags={...})` adds
  global tags to every alert; `report(extra={...})` / `capture(extra={...})`
  add ad-hoc tags to a single call, overriding `init()`'s tags on key
  collision.
- Chronic error groups (occurrences piling up while rate-limited) now back
  off exponentially: each chronic resend doubles the effective
  `rate_limit_seconds` for that group, capped at 8x. A group that goes quiet
  and reappears once (no pile-up) resets to the base rate immediately.
  `dashboard`/`--json` show the active multiplier.
- `devalerts mute <fingerprint>` / `devalerts unmute <fingerprint>`: silence
  or re-enable alerts for a specific error group without touching code.
  Unmuting resends with the accumulated skip count, same as a rate-limit
  window expiring.
- `devalerts clear <fingerprint>` / `devalerts clear --all`: delete error
  group(s) from local state. All three commands accept a unique prefix of
  the fingerprint (the `ID` column shown by `dashboard`).
- `devalerts dashboard --json`: machine-readable output for scripting.
- `devalerts test --bot-token ... --chat-id ...` CLI command: sends a one-off
  message so you can verify `bot_token`/`chat_id` are wired up correctly
  before touching any code.
- Test coverage for `ASGIMiddleware` (previously untested) and for
  `_excepthook`/`_threading_excepthook`'s "must never raise" guarantee,
  raising overall coverage from 86% to 95%.
- GitHub repo topics for discoverability.

### Fixed

- `devalerts dashboard` now uses each error group's actually configured
  `rate_limit_seconds` (persisted to the local DB) to decide `limited` vs
  `sending` status, instead of always assuming the library default.

### Changed

- `_send_telegram_message` now returns `True`/`False` instead of `None`, so
  callers (like the new `test` command) can tell whether delivery succeeded.
- Telegram delivery now retries once on failure (honoring `Retry-After` on a
  429, up to 10s) before giving up. If every attempt fails, the message is
  appended to `~/.devalerts/failed.log` instead of just being dropped.

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
