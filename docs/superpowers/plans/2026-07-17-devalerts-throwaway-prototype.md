# DevAlerts Throwaway Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the cheap validation prototype the LLM council recommended — a zero-backend Python package that sends unhandled exceptions (and manually-reported ones) straight to a Telegram chat, using a bot token the end user creates themselves. No API, no database, no queue, no accounts.

**Architecture:** A single importable package, `devalerts`. `init()` installs a global `sys.excepthook` (and `threading.excepthook` for thread coverage) that formats the exception, redacts obvious secrets, and POSTs it directly to the Telegram Bot API via stdlib `urllib`. `report()`/`capture()` cover manually-caught exceptions. No server-side component at all — every user runs this against their own bot.

**Tech Stack:** Python ≥3.9, stdlib only at runtime (`urllib`, `json`, `re`, `traceback`, `sys`, `threading`). Project managed end-to-end with `uv` (`uv init`, `uv add`, `uv run`, `uv sync`) — no direct `pip`/`venv` calls.

## Global Constraints

- Python ≥3.9 (broad compatibility with the target audience).
- Zero runtime dependencies — stdlib only. Do not add `requests` or any HTTP client library.
- All dependency/environment management goes through `uv` (`uv add`, `uv run`, `uv sync`), not raw `pip`/`venv`.
- Build backend: `uv_build` (uv's own build backend; the installed uv 0.10.2 defaults `--lib` projects to it, not `hatchling` — confirmed during Task 1 review, human decision: keep `uv_build`, no third-party build dependency needed).
- Package name: `devalerts`, importable as `import devalerts`.
- **No automated tests.** Human decision (superseding this plan's original TDD approach): no pytest, no `tests/` directory, no test steps of any kind. Each task is implementation only. Verify each task manually (e.g. `uv run python -c "..."`) as needed during implementation, but do not commit any test code or test dependency.
- Telegram hard message limit: 4096 characters — every outgoing message must be truncated to fit before sending. The final result of the formatting function must be hard-capped at this limit regardless of intermediate arithmetic (a huge exception message can make the header alone exceed the limit — clamp and hard-slice the return value, don't just subtract lengths).
- The crash handler must never itself raise. Every internal failure (network error, formatting bug) is caught and logged to stderr, never propagated — a broken alert path must not break the user's program.
- No PyPI publish in this plan. Installable via `uv add git+<repo-url>` or `pip install git+<repo-url>` once pushed; publishing is a follow-up outside this plan's scope.

---

### Task 1: Project scaffold with uv — COMPLETE

Scaffolded with `uv init --lib --name devalerts --python 3.9 .`. Produced `pyproject.toml` (uv_build backend), `src/devalerts/__init__.py` (empty `__all__`), `.gitignore`, `src/devalerts/py.typed`, `README.md` stub, `uv.lock`. Reviewed and approved (commits `1a9e0ec`, `5caa977`, `bb3a32b`). Test infrastructure (pytest dev dependency, `tests/test_devalerts.py`) was added during this task and later removed in commit `0995246` per the no-tests decision above.

**Interfaces produced:** an importable, empty `devalerts` package that every later task extends.

---

### Task 2: Alert message formatting — COMPLETE

Implemented `_MAX_MESSAGE_LENGTH = 4096` and `_format_alert(exc_type, exc_value, tb) -> str` in `src/devalerts/__init__.py` (commit `805ae28`). Renders exception type, message, and traceback into a Telegram-ready string; hard-truncates to the message limit including a backstop clamp for the case where the exception message itself is longer than the limit (verified manually — see commit message for detail).

**Interfaces produced:** `_MAX_MESSAGE_LENGTH` (module constant), `_format_alert(exc_type, exc_value, tb) -> str` — used by Task 5's `_send_exception`.

---

### Task 3: Secret redaction

**Files:**
- Modify: `src/devalerts/__init__.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_redact(text: str) -> str` — used by Task 5's `_send_exception`.

- [ ] **Step 1: Implement**

Append to `src/devalerts/__init__.py`:

```python
import re

# ponytail: fixed pattern list, not exhaustive — catches common
# token/key shapes only. Upgrade to entropy-based detection if
# real users report leaked secrets slipping through.
_REDACT_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-_.]+", re.IGNORECASE), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*\S+"),
        r"\1=[REDACTED]",
    ),
]


def _redact(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
```

- [ ] **Step 2: Verify manually**

Run: `uv run python -c "import devalerts; print(devalerts._redact('api_key=sk_live_1234567890abcdef')); print(devalerts._redact('Bearer abc123.def456')); print(devalerts._redact('AKIAABCDEFGHIJKLMNOP')); print(devalerts._redact('ValueError: user_id 42 not found'))"`

Expected: first three lines show `[REDACTED]` in place of the secret; the last line is unchanged (`ValueError: user_id 42 not found`).

- [ ] **Step 3: Commit**

```bash
git add src/devalerts/__init__.py
git commit -m "Add secret redaction for common token patterns"
```

---

### Task 4: Telegram sender

**Files:**
- Modify: `src/devalerts/__init__.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_TIMEOUT_SECONDS = 5` (module constant), `_send_telegram_message(bot_token: str, chat_id, text: str) -> None` — used by Task 5's `_send_exception`. Never raises.

- [ ] **Step 1: Implement**

Append to `src/devalerts/__init__.py`:

```python
import json
import sys
import urllib.error
import urllib.request

_TIMEOUT_SECONDS = 5


def _send_telegram_message(bot_token: str, chat_id, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"devalerts: failed to send Telegram alert: {error}", file=sys.stderr)
```

- [ ] **Step 2: Verify manually**

Verify the function builds the expected request and never raises on a network failure, without hitting the real network. Run:

```bash
uv run python -c "
import devalerts, unittest.mock, json

captured = {}

def fake_urlopen(request, timeout=None):
    captured['url'] = request.full_url
    captured['body'] = json.loads(request.data.decode('utf-8'))
    captured['timeout'] = timeout

with unittest.mock.patch('devalerts.urllib.request.urlopen', fake_urlopen):
    devalerts._send_telegram_message('TOKEN123', 42, 'hello world')

print(captured)
assert captured['url'] == 'https://api.telegram.org/botTOKEN123/sendMessage'
assert captured['body'] == {'chat_id': 42, 'text': 'hello world'}
assert captured['timeout'] == 5
print('payload OK')

import urllib.error

def broken_urlopen(request, timeout=None):
    raise urllib.error.URLError('network down')

with unittest.mock.patch('devalerts.urllib.request.urlopen', broken_urlopen):
    devalerts._send_telegram_message('TOKEN123', 42, 'hello world')  # must not raise
print('network failure did not raise: OK')
"
```

Expected: prints the captured payload, `payload OK`, and `network failure did not raise: OK` — no traceback.

- [ ] **Step 3: Commit**

```bash
git add src/devalerts/__init__.py
git commit -m "Add Telegram sender that never raises on network failure"
```

---

### Task 5: Global exception hook

**Files:**
- Modify: `src/devalerts/__init__.py`

**Interfaces:**
- Consumes: `_format_alert` (Task 2), `_redact` (Task 3), `_send_telegram_message` (Task 4).
- Produces: `_state` (module dict), `_send_exception(exc_type, exc_value, tb) -> None`, `_excepthook(exc_type, exc_value, tb) -> None`, `_threading_excepthook(args) -> None`, `init(bot_token: str, chat_id, *, redact: bool = True) -> None` — `init` is public API used by Task 6 and by end users.

- [ ] **Step 1: Implement**

Append to `src/devalerts/__init__.py`:

```python
import threading

_state = {
    "bot_token": None,
    "chat_id": None,
    "redact": True,
    "prev_excepthook": None,
    "prev_threading_excepthook": None,
}


def _send_exception(exc_type, exc_value, tb) -> None:
    message = _format_alert(exc_type, exc_value, tb)
    if _state["redact"]:
        message = _redact(message)
    _send_telegram_message(_state["bot_token"], _state["chat_id"], message)


def _excepthook(exc_type, exc_value, tb) -> None:
    if exc_type is not KeyboardInterrupt:
        try:
            _send_exception(exc_type, exc_value, tb)
        except Exception as error:  # noqa: BLE001 - crash handler must never raise
            print(f"devalerts: internal error while sending alert: {error}", file=sys.stderr)
    _state["prev_excepthook"](exc_type, exc_value, tb)


def _threading_excepthook(args) -> None:
    try:
        _send_exception(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception as error:  # noqa: BLE001
        print(f"devalerts: internal error while sending alert: {error}", file=sys.stderr)
    _state["prev_threading_excepthook"](args)


def init(bot_token: str, chat_id, *, redact: bool = True) -> None:
    """Install a global exception hook that sends unhandled exceptions to Telegram."""
    _state["bot_token"] = bot_token
    _state["chat_id"] = chat_id
    _state["redact"] = redact
    _state["prev_excepthook"] = sys.excepthook
    _state["prev_threading_excepthook"] = threading.excepthook
    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook
```

Also update `__all__` near the top of the file to `__all__ = ["init"]` (extended again in Task 6).

- [ ] **Step 2: Verify manually**

Run:

```bash
uv run python -c "
import devalerts, sys, unittest.mock

sent = []
with unittest.mock.patch.object(devalerts, '_send_exception', lambda *a: sent.append(a)):
    prev_calls = []
    sys.excepthook = lambda *a: prev_calls.append(a)
    devalerts.init('TOKEN', 42)

    # normal exception: must be sent AND chained to previous hook
    sys.excepthook(ValueError, ValueError('boom'), None)
    assert sent == [(ValueError, sent[0][1], None)]
    assert len(prev_calls) == 1
    print('chains to previous hook: OK')

    # KeyboardInterrupt must be skipped from sending, but still chained
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    assert len(sent) == 1  # unchanged
    assert len(prev_calls) == 2
    print('KeyboardInterrupt skipped from send, still chained: OK')

with unittest.mock.patch.object(devalerts, '_send_exception', lambda *a: (_ for _ in ()).throw(RuntimeError('boom'))):
    prev_calls2 = []
    sys.excepthook = lambda *a: prev_calls2.append(a)
    devalerts.init('TOKEN', 42)
    sys.excepthook(ValueError, ValueError('x'), None)  # must not raise
    assert len(prev_calls2) == 1
    print('internal send failure does not raise: OK')

sys.excepthook = sys.__excepthook__
"
```

Expected: prints all three `OK` lines, no traceback.

- [ ] **Step 3: Commit**

```bash
git add src/devalerts/__init__.py
git commit -m "Install global exception hook wired to Telegram delivery"
```

---

### Task 6: Manual reporting API

**Files:**
- Modify: `src/devalerts/__init__.py`

**Interfaces:**
- Consumes: `_send_exception` (Task 5).
- Produces: `report(exc: BaseException | None = None) -> None`, `capture` (context manager class) — both public API, used directly by end users.

- [ ] **Step 1: Implement**

Append to `src/devalerts/__init__.py`:

```python
def report(exc: BaseException | None = None) -> None:
    """Manually send a caught exception to Telegram."""
    if exc is None:
        exc_type, exc_value, tb = sys.exc_info()
        if exc_type is None:
            raise RuntimeError("report() requires an active exception or an exc argument")
    else:
        exc_type, exc_value, tb = type(exc), exc, exc.__traceback__
    _send_exception(exc_type, exc_value, tb)


class capture:
    """Context manager: report any exception raised inside the block, then re-raise it."""

    def __enter__(self) -> "capture":
        return self

    def __exit__(self, exc_type, exc_value, tb) -> bool:
        if exc_type is not None:
            _send_exception(exc_type, exc_value, tb)
        return False
```

Update `__all__` at the top of the file to its final form: `__all__ = ["init", "report", "capture"]`.

- [ ] **Step 2: Verify manually**

Run:

```bash
uv run python -c "
import devalerts, unittest.mock

sent = []
with unittest.mock.patch.object(devalerts, '_send_exception', lambda *a: sent.append(a)):
    try:
        raise ValueError('boom')
    except ValueError:
        devalerts.report()
    assert len(sent) == 1 and sent[0][0] is ValueError
    print('report() with active exception: OK')

    error = ValueError('explicit')
    devalerts.report(error)
    assert sent[1][1] is error
    print('report(exc) with explicit exception: OK')

    try:
        devalerts.report()
        print('FAIL: should have raised RuntimeError')
    except RuntimeError:
        print('report() with no active exception raises: OK')

    try:
        with devalerts.capture():
            raise ValueError('captured')
        print('FAIL: should have re-raised')
    except ValueError:
        assert len(sent) == 3
        print('capture() reports and re-raises: OK')

    before = len(sent)
    with devalerts.capture():
        pass
    assert len(sent) == before
    print('capture() does not report on success: OK')
"
```

Expected: five `OK` lines, no `FAIL`.

- [ ] **Step 3: Commit**

```bash
git add src/devalerts/__init__.py
git commit -m "Add manual report() and capture() for caught exceptions"
```

---

### Task 7: README and final check

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `init`, `report`, `capture` (public API, all prior tasks).
- Produces: nothing new — this task documents the finished package.

- [ ] **Step 1: Replace `README.md`**

Replace the full contents of `README.md` (generated stub from Task 1) with:

```markdown
# devalerts (throwaway prototype)

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

## What this does NOT do (by design — it's a throwaway prototype)

- No dashboard, no grouping/deduplication, no rate limiting — every
  unhandled exception sends a message.
- No backend, no accounts — each user runs their own bot.
- Basic secret redaction only (a few common token patterns) — do not rely
  on this for sensitive production data.
- No automated test suite — verified manually during implementation only.
```

- [ ] **Step 2: Final manual sanity check**

Run: `uv run python -c "import devalerts; print(sorted(devalerts.__all__))"`
Expected: `['capture', 'init', 'report']`

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Write usage README for the throwaway prototype"
```
