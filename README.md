# devalerts (throwaway prototype)

[Русская версия](README.ru.md)

Send unhandled Python exceptions straight to a Telegram chat. No backend,
no account, no database — just your own bot token.

## Install

    uv add git+https://github.com/<you>/<repo>.git

(or `pip install git+https://github.com/<you>/<repo>.git` if you're not using uv)

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

## What this does NOT do (by design — it's a throwaway prototype)

- No dashboard, no grouping/deduplication, no rate limiting — every
  unhandled exception sends a message.
- No backend, no accounts — each user runs their own bot.
- Basic secret redaction only (a few common token patterns) — do not rely
  on this for sensitive production data.
- No automated test suite — verified manually during implementation only.
