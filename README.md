# Android PR Reviewer

AI-сервис код-ревью для **Android** pull request'ов. Слушает GitHub webhook,
берёт diff PR, прогоняет его через LLM (DeepSeek) с Android-заточенным промптом
и постит обратно **review с вердиктом** — `APPROVE` / `REQUEST_CHANGES` / `COMMENT` —
с **inline-комментариями на конкретных строках** diff (см. ниже).

Ревью-фокус вынесен в **профили**: из коробки `android`, `compose` и `kmp`,
переключаются командой `pr-reviewer profile <имя>` (или env `REVIEW_PROFILE`).
**Свои профили** добавляются без правки кода — файлом `profiles.d/<имя>.md`
или командой `pr-reviewer profile --add` (см. ниже).

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

## Быстрый старт (визард)

Из коробки — одна команда, дальше диалог:

```bash
pip install -e .
pr-reviewer setup
```

Визард по шагам:
1. **Окружение** — проверяет Python и авторизацию `gh`.
2. **Ключ DeepSeek** — вводится **скрыто** (как пароль, символы не видны; в `gh`
   передаётся через stdin, а не в командной строке) и сохраняется в `.env`.
3. **Профиль ревью** — выбор `android` / `kmp` (список берётся из `app/profiles.py`).
4. **GitHub Actions** — если `gh` авторизован, сам предлагает настроить авто-ревью:
   ставит секрет `DEEPSEEK_API_KEY`, variable `REVIEW_PROFILE`, проверяет workflow.
   А если `gh` установлен, но не авторизован — визард предложит `gh auth login`
   прямо в процессе.

Другие команды:

```bash
pr-reviewer help                      # список команд с описанием
pr-reviewer doctor                    # проверить настройку (ключ, профиль, gh, workflow)
pr-reviewer review --pr 1 --dry-run   # разовое ревью PR из терминала (без постинга)
pr-reviewer review --repo o/n --pr 1  # ревью и публикация в PR
pr-reviewer serve                     # запустить webhook-сервис локально
```

`review` берёт токен из `GITHUB_TOKEN` или из `gh auth token`, ключ и профиль — из
`.env`. Флаги: `--profile android|kmp`, `--dry-run` (показать, не постить),
`--approve` (разрешить вердикт APPROVE — по умолчанию даунгрейдится до COMMENT,
чтобы не упереться в запрет аппрувить свой же PR).

## Структура

```
app/
  cli.py           # команды pr-reviewer: setup (визард) / doctor / serve
  main.py          # FastAPI: /webhook, /health; фон через BackgroundTasks
  security.py      # verify_signature — HMAC-SHA256 webhook-подписи
  github_client.py # get_diff() / post_review() на httpx
  reviewer.py      # ядро: diff → LLM → verdict + inline-комменты + markdown
  diff_index.py    # парсер diff → валидные строки-«якоря» для inline-комментов
  profiles.py      # профили ревью (built-in) + загрузка своих из profiles.d/
  config.py        # чтение env
profiles.d/        # свои профили: <имя>.md = инструкция «что проверять»
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

Встроенные: `android` (утечки Context, корутины, lifecycle), `compose`
(recomposition, side effects, state hoisting), `kmp` (source sets, expect/actual).

Переключение — из терминала:

```bash
pr-reviewer profile              # список + активный
pr-reviewer profile compose      # переключить локально (.env)
pr-reviewer profile kmp --sync   # + обновить repo variable для Actions
```

### Свои профили — без правки кода

Кастомный профиль — это просто файл `profiles.d/<имя>.md`, где текст файла =
инструкция «что проверять». Он подхватывается автоматически везде: в CLI,
визарде и GitHub Actions (файл коммитится в репо). Пример готового профиля —
[`profiles.d/security.md`](profiles.d/security.md).

```bash
# из файла
pr-reviewer profile --add security --from security.md
# из строки
pr-reviewer profile --add gradle --focus "Проверяй version catalogs, ..."
# интерактивно (ввод фокуса, Ctrl-D в конце)
pr-reviewer profile --add myteam
# удалить свой профиль
pr-reviewer profile --remove security
```

Профиль сразу доступен в `pr-reviewer profile`, флаге `--profile` и как
`REVIEW_PROFILE`. Свой профиль с именем встроенного его переопределяет; сами
встроенные удалить/переопределить нельзя. Формат ответа (строгий JSON → вердикт
+ inline) общий для всех профилей — его трогать не нужно.

> `profiles.d/` лежит в корне репозитория (не в `.gitignore`), поэтому свои
> профили едут в GitHub Actions вместе с кодом.

Альтернатива для «вечных» профилей — дописать в `app/profiles.py` словарь
`PROFILES` (как это сделано для `android`/`compose`/`kmp`).

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
