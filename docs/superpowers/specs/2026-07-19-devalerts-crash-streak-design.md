# DevAlerts: crash streak (счётчик «дней без инцидента») — дизайн

## Идея

Проекту нужен «фича-магнит» — что-то одновременно полезное и естественно
шарящееся (скриншот в Twitter), без нарушения философии `zero-dependency` /
`zero-infra` / "no telemetry, nothing else phones home" из README.

Решение: посчитать, сколько времени прошло с последнего зафиксированного
исключения (в духе плаката "X days since last workplace accident"), и
показать это двумя способами — в CLI-дашборде и в виде SVG-бейджа для
README. Никаких новых сетевых вызовов, никакого нового бэкенда: источник
данных уже существует в `~/.devalerts/state.db`.

## Источник данных

Без миграций схемы. Таблица `error_groups` уже хранит `last_seen` — момент
последнего появления исключения, причём это поле обновляется **на каждое
происшествие**, включая подавленные rate-limit'ом (см. `_should_send` в
`_store.py`, обе ветки — `send=True` и `send=False` — пишут `last_seen`).

Новый хелпер в `_store.py`:

```python
def _last_incident() -> float | None:
    """Unix timestamp of the most recent occurrence across all groups,
    or None if the state DB has no groups (fresh install / all cleared)."""
```

`SELECT MAX(last_seen) FROM error_groups` через уже существующий
`_get_connection()`. Пустая таблица → `None`.

## CLI: `devalerts dashboard`

Одна строка над существующей таблицей, вычисленная из `_last_incident()`:

```
🟢 14 days since the last incident.
```

```
🔴 New incident 3h ago — streak reset to 0 days.
```

```
No incidents recorded yet.
```

Правило: "new incident" (0 дней, красным) — если `now - last_incident <
86400` (т.е. в пределах последних суток), иначе зелёным считаем целые дни
(`int((now - last_incident) // 86400)`). Использует уже существующие
`_supports_unicode()`/`_color_enabled()`/`_Style` из `cli.py` для fallback на
ASCII/no-color терминалах, тем же образом, что и остальной дашборд.

`--json` вывод `dashboard` **не меняется** — не ломаем существующий контракт
(это просто массив групп, без обёртки). Если позже понадобится машиночитаемый
стрик — отдельная итерация, не эта.

## CLI: новая команда `devalerts badge`

```
devalerts badge [--out PATH] [--label TEXT]
```

- Без `--out` — печатает SVG в stdout.
- `--out PATH` — пишет в файл (для CI, которая коммитит бейдж в репозиторий).
- `--label TEXT` — левая часть бейджа, по умолчанию `"crash streak"`.

Рисуется вручную (без сети, без shields.io API) — плоский SVG-шаблон,
структурно похожий на shields.io flat badge (два прямоугольника + текст),
собранный f-string'ами в новом модуле `src/devalerts/_badge.py`:

```python
def _render_badge(label: str, days: int | None) -> str:
    """days=None -> 'no incidents yet' (grey);
    days==0 -> 'today' (red); 1-6 -> yellow; >=7 -> green."""
```

Цветовые пороги — те же, что и в тексте дашборда (red/yellow/green), плюс
серый для "ещё не было инцидентов" — четвёртое состояние, которого в
однострочном тексте дашборда нет (там просто "No incidents recorded yet.").

## Документация

- README.md / README.ru.md: новая секция "Crash streak badge" сразу после
  секции про дашборд — пример бейджа, короткий сниппет GitHub Actions
  (cron job: `devalerts badge --out docs/streak.svg` + commit), поясняющий,
  как бейдж "живёт" в публичном репо пользователя без какого-либо бэкенда
  со стороны devalerts.
- CHANGELOG.md: секция `## [Unreleased]`.

## Редактирование существующего кода

- `_store.py` — добавить `_last_incident()`.
- `cli.py` — `_dashboard()` печатает новую строку перед таблицей; `main()`
  получает новый субпарсер `badge` с `--out`/`--label`.
- Новый файл `_badge.py` — `_render_badge()` + общая функция определения
  "полосы" (`red`/`yellow`/`green`/`grey`) по количеству дней, переиспользуемая
  и текстовой строкой дашборда, и SVG (одна функция вместо дублирования
  порогов в двух местах).

## Тесты

- `tests/test_store.py`: `_last_incident()` — `None` на пустой таблице;
  после нескольких вызовов `_should_send()` возвращает `last_seen` последней
  по времени группы (используется реальный временный SQLite-файл, как и
  остальные тесты в этом файле — без моков `sqlite3`).
- `tests/test_cli.py`: `_dashboard()` печатает ожидаемую строку для трёх
  случаев (нет групп / инцидент сегодня / инцидент N дней назад — через
  monkeypatch `time.time()` или прямую вставку `last_seen` в тестовую БД);
  `_render_badge()` — корректный цвет/текст для `None`/`0`/`3`/`10` дней.

## Границы этой итерации (сознательно не делаем)

- Никакой автоматизации хостинга/публикации бейджа со стороны devalerts —
  только генерация файла, регенерация и коммит целиком на стороне
  пользователя (документируем сниппет, не делаем встроенный GitHub Action).
- Стрик — глобальный по всей `state.db`, без разбивки по тегам/окружениям.
- `dashboard --json` не расширяется — при необходимости это отдельная,
  более поздняя итерация.
- Никакого вызова внешних сервисов (LLM, shields.io и т.п.) — эта идея была
  предложена и отклонена на этапе брейнсторминга.
