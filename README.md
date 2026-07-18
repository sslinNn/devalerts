# devalerts

[![PyPI](https://img.shields.io/pypi/v/devalerts)](https://pypi.org/project/devalerts/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/devalerts?period=total&units=INTERNATIONAL_SYSTEM&left_color=GREY&right_color=BLUE&left_text=downloads)](https://pepy.tech/projects/devalerts)
[![Python versions](https://img.shields.io/pypi/pyversions/devalerts)](https://pypi.org/project/devalerts/)
[![License: MIT](https://img.shields.io/pypi/l/devalerts)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/sslinNn/devalerts/test.yml?branch=main&label=tests)](https://github.com/sslinNn/devalerts/actions/workflows/test.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy](https://img.shields.io/badge/mypy-checked-blue)](https://mypy-lang.org/)

[Русская версия](README.ru.md)

Send unhandled Python exceptions straight to a Telegram chat — the moment
they happen, on your phone. No backend, no account, no database — just your
own bot token.

```python
import devalerts

devalerts.init(bot_token="123456:ABC-DEF...", chat_id=123456789)
```

That's the whole setup. Two minutes with [@BotFather](https://t.me/BotFather)
and every unhandled crash — including ones raised in threads — lands in your
chat instead of a log file nobody's watching.

## Why devalerts

- **Zero infrastructure.** No SaaS signup, no ingestion server, no API key
  to manage beyond your own Telegram bot token. State lives in a local
  SQLite file you already own.
- **One line to install, one line to wire up.** `init()` installs the hook
  and gets out of the way.
- **Not spam.** Errors are grouped by fingerprint and rate-limited per
  group, so a crash loop sends one message, not a thousand.
- **Framework-aware.** Ships an ASGI middleware for FastAPI/Starlette apps,
  where the default excepthook would never even see a request error.
- **Small and typed.** No dependencies, ships `py.typed`, ~450 lines total —
  short enough to read in one sitting before you trust it with your errors.

## Install

    uv add devalerts

(or `pip install devalerts` if you're not using uv)

## Usage

1. Create a bot with [@BotFather](https://t.me/BotFather) and get its token.
2. Message your bot once (or add it to a group) so it's allowed to message you back.
3. Get your chat id — message [@userinfobot](https://t.me/userinfobot), or call
   `https://api.telegram.org/bot<TOKEN>/getUpdates` after step 2 and read `message.chat.id`.
4. Verify it's wired up correctly before touching any code:

   ```
   uv run devalerts test --bot-token 123456:ABC-DEF... --chat-id 123456789
   ```

5. In your app, as early as possible:

```python
import devalerts

devalerts.init(bot_token="123456:ABC-DEF...", chat_id=123456789)
```

That's it — any unhandled exception (including ones raised in threads) now
also lands in your Telegram chat.

The traceback itself arrives folded into a collapsed quote — the exception
type, message, and host are visible right away, tap to expand the full
traceback. Keeps a big stack trace from taking over the chat.

## Grouping, rate limiting, and the dashboard

Exceptions are grouped by fingerprint (exception type + file + line where it
was raised) in a local SQLite file (`~/.devalerts/state.db`). Each group
sends at most one Telegram message per `rate_limit_seconds` (default 300);
repeats inside that window are counted but not sent, and the next message
for that group says how many were skipped. Old groups (untouched for 7 days)
are pruned automatically. Configure the window via `init()`:

```python
devalerts.init(bot_token="...", chat_id=123456789, rate_limit_seconds=60)
```

See what's grouped and what's currently rate-limited:

```
uv run devalerts dashboard
```

![devalerts dashboard output](https://raw.githubusercontent.com/sslinNn/devalerts/main/docs/dashboard.svg)

Pass `--json` for machine-readable output. Silence a noisy group without
touching code — the `ID` column accepts any unique prefix:

```
uv run devalerts mute abc12345
uv run devalerts unmute abc12345
uv run devalerts clear abc12345   # or: devalerts clear --all
```

Unmuting resends the next occurrence with the accumulated skip count, same
as a rate-limit window expiring.

Error groups that keep piling up while suppressed are chronic: each such
resend doubles the effective `rate_limit_seconds` for that group (capped at
8x), so a crash loop backs itself off instead of paging you every window
forever. A group that goes quiet and reappears once resets to the base rate
immediately. The dashboard shows the active multiplier (`● sending ×4`).

## Context: hostname and tags

Every alert automatically includes the sending host, so you can tell which
process/server it came from when one bot serves several:

```python
devalerts.init(bot_token="...", chat_id=123456789, tags={"env": "production"})
```

```
🔴 ValueError: boom
🖥️ prod-web-2 (env=production)
```

Add ad-hoc tags to a single call — they override `init()`'s tags (and each
other) on a key collision:

```python
devalerts.report(extra={"request_id": "abc123"})
```

Used as a decorator, `capture()` tags the alert with the wrapped function's
name as `job` automatically — no `extra` needed:

```python
@devalerts.capture()
def nightly_sync(): ...
```

```
🔴 ValueError: boom
🖥️ prod-web-2 (job=nightly_sync)
```

Pass `extra={"job": "..."}` explicitly to override the auto-detected name.

## Manually reporting a caught exception

```python
try:
    risky_call()
except Exception:
    devalerts.report()  # sends the currently-handled exception
```

or:

```python
with devalerts.capture():
    risky_call()  # reports on exception, then re-raises
```

`capture` also works as a decorator, so you don't need to touch a function's
body at all:

```python
@devalerts.capture()
def risky_call():
    ...
```

## FastAPI / Starlette / any ASGI app

`init()`'s excepthook won't see request errors — the framework already catches
them internally to return a 500 response, so nothing "unhandled" ever reaches
the process. Use the ASGI middleware instead:

```python
app.add_middleware(devalerts.ASGIMiddleware)
```

Only exceptions that actually escape as server errors get reported — routing
404s and raised `HTTPException`s are already turned into responses by the
framework before the middleware sees them.

## Celery

Same problem as ASGI apps: `init()`'s excepthook never sees exceptions raised
inside a task, because Celery catches them itself to record the task's
`FAILURE` state. Call `init_celery()` in addition to `init()`:

```python
devalerts.init(bot_token="...", chat_id=123456789)
devalerts.init_celery()
```

This connects to Celery's `task_failure` signal (fired once a task has
genuinely failed — retries exhausted or none configured, so retried tasks
don't spam an alert per attempt) and reports through the same
grouping/rate-limiting/redaction path as everything else, tagged with the
task name and id automatically. Requires Celery to already be installed in
the worker process — it's imported lazily, not a devalerts dependency.

## Why not Sentry?

If you already run Sentry/Rollbar/etc., keep using it — devalerts isn't a
replacement. It's for the side project, internal tool, or small service that
doesn't have (and doesn't want) that infrastructure: no account to create, no
SDK to configure, no server to trust — just a bot token you already control.

|                          | devalerts          | Sentry-style tracker |
|--------------------------|---------------------|-----------------------|
| Setup                    | one bot token       | account + project + SDK config |
| Backend                  | none — Telegram only | hosted or self-hosted service |
| Where alerts land        | your Telegram chat  | a web dashboard |
| Grouping / rate limiting | yes, local SQLite   | yes, server-side |
| Search, trends, releases | no                  | yes |

## FAQ

**Works with FastAPI / Starlette?**
Yes — use [`ASGIMiddleware`](#fastapi--starlette--any-asgi-app), since the
default excepthook never sees request errors.

**Works with Docker?**
Yes, nothing container-specific. Just make sure `~/.devalerts/` (the dedup
state file) is either writable inside the container or a mounted volume if
you want dedup state to survive restarts — it self-recreates otherwise.

**Works with threads?**
Yes — `init()` installs both `sys.excepthook` and `threading.excepthook`.

**Works with Celery / background workers?**
Yes — call [`init_celery()`](#celery) in addition to `init()` to catch
exceptions raised inside tasks, which the excepthook alone won't see.

**Works on Windows / Linux / macOS?**
Yes — stdlib only (`urllib`, `sqlite3`, `threading`), no OS-specific code
paths.

## Privacy & Security

- The only network call devalerts makes is to `api.telegram.org` — no
  telemetry, no analytics, nothing else phones home.
- No third-party server and no devalerts-run backend — messages go straight
  from your process to your own Telegram bot.
- No accounts, no signup, no API key beyond the bot token you create and
  control yourself.
- Basic secret redaction only (a few common token/key patterns) — do not
  rely on this for sensitive production data; scrub what you can before it
  ever reaches an exception message.
- If Telegram delivery fails after retrying, the alert (already redacted, if
  `redact=True`) is appended to `~/.devalerts/failed.log` instead of being
  dropped — clean it up like any other local log file.

## What this does NOT do (by design)

- Grouping/rate limiting is local and in-process only (SQLite file, no
  server) — the dashboard is a CLI table, not a web UI.
- No backend, no accounts — each user runs their own bot.

## Roadmap

- Web dashboard (hosted, optional — the local CLI dashboard stays either way)
- Slack delivery
- Discord delivery
- Email delivery

## Development

    uv sync --group dev
    uv run pre-commit install

`pre-commit` runs `ruff check`, `ruff format`, and `mypy` before each commit.
Run the full check manually with:

    uv run pytest
    uv run pre-commit run --all-files

## License

MIT — see [LICENSE](LICENSE).
