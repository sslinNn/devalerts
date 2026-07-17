# DevAlerts: дедупликация, rate-limit и CLI-дашборд — дизайн

## Идея

Прототип (`docs/superpowers/specs/2026-07-16-devalerts-mvp-design.md`) сознательно
не делал группировку/дедупликацию, rate-limit и дашборд — README прямо перечисляет
это как границу «throwaway». Полный SaaS-дизайн из того же документа решает эти
три вещи через отдельный бэкенд (Postgres + Redis + worker + Mini App), но переход
на него — отдельный, гораздо более крупный проект.

Эта итерация добавляет все три фичи **без бэкенда**, оставаясь в рамках модели
«одна библиотека, ноль внешней инфраструктуры»: пользователь по-прежнему просто
вызывает `devalerts.init(...)` в своём процессе.

## Архитектура

Все существующие точки входа (`_excepthook`, `_threading_excepthook`, `report()`,
`capture()`, `ASGIMiddleware`) продолжают вызывать `_send_exception()`. Внутри неё
появляется новый шаг: перед отправкой в Telegram запрос проходит через
`_should_send(fingerprint, ...) -> (bool send, int skipped)`, читающую и
обновляющую локальный SQLite-файл `~/.devalerts/state.db` (stdlib `sqlite3`,
`PRAGMA journal_mode=WAL` для конкурентного чтения дашбордом во время записи
приложением). Никакого нового процесса или сетевого сервиса.

```
exception ─▶ _send_exception() ─▶ _should_send() ──▶ SQLite (~/.devalerts/state.db)
                                        │
                              send=True │ send=False
                                        ▼
                         _format_alert() + Telegram         (молча инкрементит счётчик)
```

## Данные (SQLite, таблица `error_groups`)

| колонка | тип | смысл |
|---|---|---|
| `fingerprint` | TEXT PK | `sha1(f"{exc_type_name}:{file}:{lineno}")[:16]` — по верхнему фрейму traceback (месту, где реально бросило исключение) |
| `exc_type` | TEXT | имя типа исключения, для дашборда |
| `location` | TEXT | `"file:line"`, для дашборда |
| `first_seen` | REAL | unix timestamp первого появления |
| `last_seen` | REAL | unix timestamp последнего появления (любого, не только отправленного) — ключ для TTL-очистки |
| `last_sent` | REAL \| NULL | unix timestamp последней реально отправленной Telegram-alert |
| `count_since_last_sent` | INTEGER | сколько раз произошло с последней отправки, но алерт был подавлен rate-limit'ом |
| `total_count` | INTEGER | всего произошло, для дашборда |

Fingerprint — простой хэш сигнатуры, без ML/эвристик (согласовано с уже принятым
подходом полного SaaS-дизайна).

## Логика send/skip и очистка

```python
now = time.time()
row = SELECT * FROM error_groups WHERE fingerprint = ?

if row is None or row.last_sent is None or now - row.last_sent >= rate_limit_seconds:
    send = True
    skipped = row.count_since_last_sent if row else 0
    UPSERT error_groups: last_sent=now, count_since_last_sent=0,
                          total_count+=1, last_seen=now
else:
    send = False
    UPDATE error_groups: count_since_last_sent+=1, total_count+=1, last_seen=now

DELETE FROM error_groups WHERE last_seen < now - _RETENTION_SECONDS
```

- Свежее `sqlite3.connect()` на каждый вызов (потокобезопасно per-connection без
  ручных локов; дешевле, чем сам сетевой запрос в Telegram, которым это и так
  ограничено).
- Очистка — не отдельный поток/крон, просто `DELETE` при каждой записи с индексом
  на `last_seen`. `_RETENTION_SECONDS` = 7 дней, фиксировано (не параметр `init()`
  в этой итерации).
- Повторы в окне rate-limit не теряются — `total_count`/`count_since_last_sent`
  инкрементятся всегда, просто не триггерят сообщение.

Когда `send=True` и `skipped > 0`, `_format_alert()` добавляет строку:
`⚠️ Повторилась ещё {skipped} раз(а) с последнего алерта`.

## Публичный API

`init()` получает новый keyword-параметр:

```python
devalerts.init(bot_token=..., chat_id=..., rate_limit_seconds=300)
```

`rate_limit_seconds` — окно на одну группу (fingerprint), по умолчанию 300 (5 минут).
Путь к БД не параметризуется — фиксированный `~/.devalerts/state.db`.

## CLI-дашборд

Новый модуль `src/devalerts/cli.py`, entry point `devalerts` через
`[project.scripts]` в `pyproject.toml`. Команда:

```
devalerts dashboard
```

Открывает `~/.devalerts/state.db` read-only, печатает таблицу групп, отсортированную
по `last_seen` убыв.:

```
FINGERPRINT  TYPE           LOCATION              LAST SEEN            TOTAL  IN WINDOW
a1b2c3d4...  ValueError     app.py:42             2026-07-17 14:02:11  340    yes
```

`IN WINDOW = yes/no` — сейчас ли группа подавлена rate-limit'ом (наглядно показывает
разницу между «что реально произошло» и «что видно в Telegram»).

Веб-версия дашборда (`http.server` на localhost) — намеренно вне этой итерации,
следующий шаг по мере необходимости.

## Редактирование существующего кода

- `_send_exception()` (`src/devalerts/__init__.py`) — добавляется вызов
  `_should_send()` перед `_send_telegram_message()`; при `send=False` — ранний возврат.
  Затрагивает всех вызывающих (`_excepthook`, `_threading_excepthook`, `report`,
  `capture`, `ASGIMiddleware`) без изменения их сигнатур — дедупликация становится
  прозрачной для всех существующих точек входа разом (не патчится в каждом вызывающем
  по отдельности).
- `_format_alert()` — новый опциональный параметр `skipped: int = 0` для строки
  о повторах.
- `README.md` / `README.ru.md` — убрать «No dashboard, no grouping/deduplication,
  no rate limiting» из списка «What this does NOT do», задокументировать
  `rate_limit_seconds` и `devalerts dashboard`.

## Границы этой итерации (сознательно не делаем)

- Нет веб-дашборда — только CLI-таблица.
- `_RETENTION_SECONDS` фиксирован (7 дней), не настраивается.
- Путь к SQLite-файлу фиксирован, не настраивается.
- Нет автотестов — сохраняется решение из плана прототипа (человек уже зафиксировал
  «no automated tests», верификация вручную через `uv run python -c "..."`).
- Не переезжаем на архитектуру полного SaaS-дизайна (Postgres/Redis/worker/Mini App).
