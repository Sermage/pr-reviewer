# Android PR Reviewer

AI-сервис код-ревью для **Android** pull request'ов. Слушает GitHub webhook,
берёт diff PR, прогоняет его через LLM (DeepSeek) с Android-заточенным промптом
и постит обратно **review с вердиктом** — `APPROVE` / `REQUEST_CHANGES` / `COMMENT` —
с **inline-комментариями на конкретных строках** diff (см. ниже).

Ревью-фокус вынесен в **профили**: из коробки `android` и `kmp`, переключается
одной env-переменной `REVIEW_PROFILE`, новые добавляются в `app/profiles.py`
без правки остальной логики.

```
GitHub PR (opened/synchronize)
      │  webhook POST + HMAC-подпись (X-Hub-Signature-256)
      ▼
FastAPI /webhook  ──▶ 202 сразу, ревью считается в фоне
      │  1) verify_signature   2) get PR diff (GitHub API)
      │  3) review_diff (DeepSeek, профиль)   4) post_review
      ▼
Review-комментарий в PR
```

## Структура

```
app/
  main.py          # FastAPI: /webhook, /health; фон через BackgroundTasks
  security.py      # verify_signature — HMAC-SHA256 webhook-подписи
  github_client.py # get_diff() / post_review() на httpx
  reviewer.py      # ядро: diff → LLM → verdict + inline-комменты + markdown
  diff_index.py    # парсер diff → валидные строки-«якоря» для inline-комментов
  profiles.py      # профили ревью: android, kmp, ... (точка расширения)
  config.py        # чтение env
scripts/demo_local.py  # прогон на локальном .diff без GitHub (для демо)
samples/sample.diff    # пример с GlobalScope-утечкой
tests/                 # моки webhook/LLM/diff — работают без сети
```

## Запуск локально

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # впиши ключи

uvicorn app.main:app --reload --port 8000
```

### Прокинуть webhook на localhost (Mac)
GitHub не достучится до `localhost`, поэтому туннель:

```bash
# вариант smee.io (рекомендует GitHub)
npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8000/webhook
# или ngrok
ngrok http 8000
```

Публичный URL → в настройки репозитория **Settings → Webhooks**:
Payload URL = `<url>/webhook`, Content type = `application/json`,
Secret = твой `WEBHOOK_SECRET`, событие = **Pull requests**.

## Демо без GitHub

Весь конвейер ревью можно прогнать на сохранённом diff — удобно для защиты:

```bash
LLM_API_KEY=sk-... python scripts/demo_local.py samples/sample.diff
# переключить профиль:
python scripts/demo_local.py samples/sample.diff --profile kmp
```

А чтобы гонять и webhook-путь без постинга в GitHub — `POST_REVIEWS=false` (dry-run,
результат уходит в лог).

## Тесты

```bash
pytest
```

Тесты не ходят в сеть: webhook-подпись, маппинг вердиктов, выбор профиля и
обработчик `/webhook` проверяются на моках.

## Аутентификация: сейчас и потом

- **Сейчас (MVP):** Personal Access Token (`GITHUB_TOKEN`) с scope `repo`. Просто и
  достаточно для одного аккаунта/демо.
- **Дальше:** GitHub App (JWT → installation token) — ставится на любой репозиторий,
  свой бот-аватар. `github_client.py` изолирует работу с API, так что миграция
  затрагивает только слой аутентификации.

## Профили ревью (точка расширения)

Добавить новый фокус (напр. `compose`, `gradle`) — дописать в `app/profiles.py`:

```python
PROFILES["compose"] = Profile("compose", COMPOSE_FOCUS)
```

и выставить `REVIEW_PROFILE=compose`. Формат ответа (строгий JSON → вердикт)
общий для всех профилей, его трогать не нужно.

## Inline-комментарии

Замечания вешаются **на конкретные строки** diff, а не только общим блоком.
Модель для каждого замечания возвращает `file` + `line` (строка в новой версии
файла), и сервис постит их как inline review comments.

GitHub роняет **весь** review с ошибкой `422`, если inline-коммент указывает на
строку, которой нет в diff. Поэтому:

1. **`app/diff_index.py`** парсит unified diff и собирает по каждому файлу
   множество допустимых строк-«якорей» — добавленные (`+`) и контекстные (` `)
   строки на правой (новой) стороне diff.
2. **`reviewer.py`** делит замечания: те, что попали на валидный якорь → уходят
   в inline-комментарии; остальные не теряются, а сводятся в тело ревью
   (раздел «Прочие замечания»).
3. **`github_client.post_review`** отправляет `comments`, а при `422`
   **откатывается на ревью без inline** — один плохой якорь не «съест» весь отзыв.

```
issue {file, line, note}
        │
        ▼  line ∈ commentable_lines(diff)[file] ?
   ┌────┴─────┐
  да          нет
   │            │
inline       в тело ревью
```

Пример живого ревью с inline-комментами — [PR #1](https://github.com/Sermage/pr-reviewer/pull/1).

## Статус

MVP. Ключи GitHub/DeepSeek подставляются через `.env`; без них работает демо-режим
на моках (`pytest`, `demo_local.py` с фейковым ключом упадёт только на реальном
вызове LLM — сам конвейер и выбор профиля проверяются офлайн).
