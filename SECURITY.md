# Security Policy

## Supported Versions

Only the latest published release on [PyPI](https://pypi.org/project/devalerts/)
is supported. There's no long-term support branch — please upgrade before
reporting.

## Reporting a Vulnerability

Please don't open a public GitHub issue for security reports. Instead, email
**morison1991@mail.ru** with a description of the issue and, if possible,
steps to reproduce it. You should get a response within a few days.

## Scope

devalerts makes exactly one network call: to `api.telegram.org`, using the
bot token and chat id you provide to `init()`. It has no accounts, no
backend of its own, and no telemetry. Secret redaction
(`src/devalerts/_alert.py`) covers a few common token/key patterns only —
it is a best-effort convenience, not a guarantee. Do not treat it as a
substitute for scrubbing sensitive data before it reaches an exception
message.
