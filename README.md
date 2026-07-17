# devalerts

[![PyPI](https://img.shields.io/pypi/v/devalerts)](https://pypi.org/project/devalerts/)
[![Python versions](https://img.shields.io/pypi/pyversions/devalerts)](https://pypi.org/project/devalerts/)
[![License: MIT](https://img.shields.io/pypi/l/devalerts)](LICENSE)
[![Tests](https://github.com/sslinNn/devalerts/actions/workflows/test.yml/badge.svg)](https://github.com/sslinNn/devalerts/actions/workflows/test.yml)

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
4. In your app, as early as possible:

```python
import devalerts

devalerts.init(bot_token="123456:ABC-DEF...", chat_id=123456789)
```

That's it — any unhandled exception (including ones raised in threads) now
also lands in your Telegram chat.

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

```
  ID        TYPE          LOCATION                            LAST SEEN TOTAL   STATUS
  ────────────────────────────────────────────────────────────────────────────────────
  a1b2c3d4  ValueError    app/orders.py:42                     2m ago       14   ● limited
  9f8e7d6c  KeyError      app/handlers/webhook.py:88            just now      1   ● sending

  2 error groups, 1 currently rate-limited.
```

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

## devalerts vs. a full error tracker

If you already run Sentry/Rollbar/etc., keep using it — this isn't a
replacement. devalerts is for the side project, internal tool, or small
service that doesn't have (and doesn't want) that infrastructure yet:

|                          | devalerts          | Sentry-style tracker |
|--------------------------|---------------------|-----------------------|
| Setup                    | one bot token       | account + project + SDK config |
| Backend                  | none — Telegram only | hosted or self-hosted service |
| Where alerts land        | your Telegram chat  | a web dashboard |
| Grouping / rate limiting | yes, local SQLite   | yes, server-side |
| Search, trends, releases | no                  | yes |

## What this does NOT do (by design)

- Grouping/rate limiting is local and in-process only (SQLite file, no
  server) — the dashboard is a CLI table, not a web UI.
- No backend, no accounts — each user runs their own bot.
- Basic secret redaction only (a few common token patterns) — do not rely
  on this for sensitive production data.

## Development

    uv sync --group dev
    uv run pre-commit install

`pre-commit` runs `ruff check`, `ruff format`, and `mypy` before each commit.
Run the full check manually with:

    uv run pytest
    uv run pre-commit run --all-files

## License

MIT — see [LICENSE](LICENSE).
