# Архитектура Android PR Reviewer

Техническая документация: как сервис устроен внутри. Пользовательская
инструкция — в [`README.md`](../README.md).

## Общий конвейер

```
GitHub PR (opened/synchronize/reopened)
      │  webhook POST + HMAC-подпись (X-Hub-Signature-256)
      ▼
FastAPI /webhook  ──▶ 202 сразу, ревью считается в фоне (BackgroundTasks)
      │  1) verify_signature      2) get PR diff (GitHub API)
      │  3) review_diff (LLM, профиль/оркестратор)   4) post_review
      ▼
Review с вердиктом + inline-комментарии в PR
```

Тот же «мозг» (`app/runner.py` → `review_diff`) переиспользуется тремя точками
входа: webhook (`app/main.py`), CLI (`pr-reviewer review`) и CI-скрипт
(`scripts/review_pr.py`). Логика ревью живёт ровно в одном месте.

## Карта модулей

```
app/
  main.py          # FastAPI: /webhook, /health; фон через BackgroundTasks
  cli.py           # CLI pr-reviewer: setup/doctor/review/profile/provider/serve/help
  runner.py        # общий конвейер: get_diff → review_diff → post_review
  reviewer.py      # ядро: diff → LLM → verdict + inline-комменты + markdown
  orchestrator.py  # мета-профиль auto: детект направлений → агенты → слияние
  providers.py     # LLM-провайдеры: deepseek/openai/claude/local (2 протокола)
  profiles.py      # профили ревью (built-in) + загрузка своих из profiles.d/
  diff_index.py    # парсер diff → валидные строки-«якоря» для inline-комментов
  github_client.py # get_diff() / post_review() на httpx
  security.py      # verify_signature — HMAC-SHA256 webhook-подписи
  config.py        # чтение env → Settings (резолвит провайдера)
profiles.d/        # свои профили: <имя>.md = инструкция «что проверять»
scripts/
  review_pr.py     # CI-entrypoint (GitHub Actions)
  demo_local.py    # прогон на локальном .diff без GitHub
samples/sample.diff  # пример diff с GlobalScope-утечкой
tests/               # моки webhook/LLM/diff — работают без сети
```

## Webhook и безопасность

- `POST /webhook` проверяет подпись `X-Hub-Signature-256` (HMAC-SHA256 от тела с
  общим секретом `WEBHOOK_SECRET`, сравнение постоянного времени в
  `security.verify_signature`). Пустой секрет отключает проверку (для локальных
  моков).
- `ping` → `pong`; события кроме `pull_request` и действия вне
  `REVIEW_ACTIONS` (`opened`/`synchronize`/`reopened`) игнорируются.
- GitHub ждёт ответ ~10 секунд и ретраит, поэтому обработчик отвечает `202`
  сразу, а ревью уходит в `BackgroundTasks` (`run_review`).

## Ядро ревью (`reviewer.py`)

1. Diff обрезается до `_MAX_DIFF_CHARS` (30k символов) — защита от переполнения
   контекста.
2. Модель получает system-промпт профиля + сам diff и обязана ответить **строгим
   JSON** по контракту (`_JSON_CONTRACT` в `profiles.py`): `verdict`, `summary`,
   `issues[]` с `file`/`line`/`severity`/`note`.
3. `verdict` маппится на GitHub review event:
   `approve → APPROVE`, `request_changes → REQUEST_CHANGES`, `comment → COMMENT`.
4. `issues` делятся на inline-комментарии и «прочие» (см. ниже), собирается
   markdown-тело, возвращается `ReviewResult(event, body, verdict, comments,
   summary, leftover)`.

`allow_approve=False` (в `runner`) даунгрейдит `APPROVE → COMMENT` — нужно, когда
токен не может аппрувить (токен GitHub Actions или свой же PR).

## Inline-комментарии (`diff_index.py`)

GitHub роняет **весь** review с ошибкой `422`, если inline-коммент указывает на
строку, которой нет в diff. Поэтому:

1. `commentable_lines(diff)` парсит unified diff и собирает по каждому файлу
   множество допустимых строк-«якорей» — добавленные (`+`) и контекстные (` `)
   строки на правой (новой) стороне.
2. `reviewer._split_issues` делит замечания: попавшие на валидный якорь → в
   inline-комментарии; остальные не теряются, а сводятся в тело (раздел «Прочие
   замечания»).
3. `github_client.post_review` отправляет `comments`, а при `422`
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

## Провайдеры LLM (`providers.py`)

Всего **два протокола**:

- **`openai`** — OpenAI-совместимый `POST {base}/chat/completions` (DeepSeek,
  OpenAI и локальные серверы: Ollama, LM Studio, vLLM). Заголовок
  `Authorization: Bearer` опускается, если ключа нет (локальные модели).
- **`anthropic`** — Claude `POST {base}/v1/messages` (заголовки `x-api-key`,
  `anthropic-version`, поле `system`, ответ в `content[].text`).

Провайдер — это именованный **пресет** (`Provider`: протокол + `base_url` +
модель по умолчанию + `json_mode` + `needs_key`):

| Провайдер | Протокол | Endpoint | Модель | Ключ | json_mode |
|---|---|---|---|---|---|
| `deepseek` | openai | `https://api.deepseek.com` | `deepseek-chat` | нужен | on |
| `openai` | openai | `https://api.openai.com/v1` | `gpt-4o-mini` | нужен | on |
| `claude` | anthropic | `https://api.anthropic.com` | `claude-sonnet-5` | нужен | off |
| `local` | openai | `http://localhost:11434/v1` | `qwen2.5-coder` | не нужен | off |

`resolve()` сливает пресет с override'ами `LLM_BASE_URL`/`LLM_MODEL`/
`LLM_JSON_MODE` → `LLMConfig`. Пустые override'ы берут значения пресета, поэтому
старый `.env` только с `LLM_API_KEY` продолжает работать (дефолт — `deepseek`).

**json_mode**: где поддерживается — шлём `response_format=json_object` (строгий
JSON от OpenAI/DeepSeek). Для Claude и локальных моделей ответ парсится
устойчиво — `_extract_json` снимает ` ```json … ``` ` обёртку перед `json.loads`.

Добавить ещё один OpenAI-совместимый сервис — это новый пресет в `PROVIDERS`
или `provider local --base-url <url>`, кода трогать не нужно.

## Оркестратор профилей (`orchestrator.py`, мета-профиль `auto`)

`review_diff(profile="auto")` делегирует в `orchestrate_review` (ленивый импорт,
чтобы не было цикла `reviewer ↔ orchestrator`). Шаги:

1. **Детект** — `detect_topics(diff)` считает regex-сигналы направлений
   (`compose`, `kmp`, `network`, `database`, `security`, `android`) по diff.
   Быстро и без LLM-вызова. `android` — широкий baseline, идёт последним.
2. **План** — `plan_review(diff, available)`:
   - `chosen` = найденные направления, у которых есть профиль (built-in или свой
     из `profiles.d/`), отсортированные по счёту, не больше `MAX_AGENTS` (=3);
   - `missing` = найденные направления без профиля;
   - если ничего не выбрано — `chosen = [default]`.
3. **Агенты** — на каждый профиль из `chosen` параллельно (`asyncio.gather`)
   запускается `review_diff` с конкретным профилем.
4. **Слияние** (`_merge`):
   - вердикт — худший из агентов (`REQUEST_CHANGES` > `COMMENT` > `APPROVE`);
   - inline-комментарии объединяются, дедуп по `(path, line, body)`, при
     нескольких профилях помечаются тегом `[<профиль>]`;
   - тело — секции по профилям + предупреждения по каждому `missing`
     направлению («точность снижена, добавьте профиль `--add <направление>`»).

Единичный профиль без `missing` возвращается как есть (без обёртки оркестратора)
— обычный PR остаётся чистым.

## Профили (`profiles.py`)

- `Profile(name, focus)` → `system_prompt = focus + _JSON_CONTRACT`. Профили
  описывают только *что ревьюить*, формат ответа общий.
- Built-in: `android`, `compose`, `kmp` (словарь `PROFILES`).
- `load_profiles()` мёржит built-in с файлами `profiles.d/*.md` (тело файла =
  focus, имя файла = имя профиля; свой с именем built-in переопределяет его).
- `profiles.d/` лежит в корне репозитория (не в `.gitignore`), поэтому свои
  профили едут в GitHub Actions вместе с кодом.
- `AUTO_PROFILE = "auto"` — мета-имя, обрабатывается оркестратором, а не как
  реальный focus.

## Конфигурация (`config.py`)

`load_settings()` читает env и через `providers.resolve()` заполняет
`Settings` (github_token, webhook_secret, llm_* с уже разрешённым провайдером,
review_profile, post_reviews, review_actions). `POST_REVIEWS=false` гоняет весь
конвейер без записи в GitHub (dry-run в лог).

## GitHub Actions (`.github/workflows/ai-review.yml`)

- Триггер: `pull_request` (`opened`/`synchronize`/`reopened`),
  `permissions: pull-requests: write`.
- Ключ — секрет `LLM_API_KEY` (fallback на старый `DEEPSEEK_API_KEY`).
- Провайдер и профиль — repo variables `LLM_PROVIDER`/`LLM_MODEL`/`LLM_BASE_URL`/
  `REVIEW_PROFILE`.
- `scripts/review_pr.py` резолвит repo/PR из аргументов/env/события и вызывает
  общий `runner.review_pr`. Токен Actions **не может аппрувить** PR, поэтому под
  `GITHUB_ACTIONS=true` `APPROVE` даунгрейдится в `COMMENT`.
- `local` в Actions не работает — runner не достучится до `localhost`.

## Аутентификация: сейчас и дальше

- **Сейчас (MVP):** Personal Access Token (`GITHUB_TOKEN`) со scope `repo`.
  Просто и достаточно для одного аккаунта/демо.
- **Дальше:** GitHub App (JWT → installation token) — ставится на любой
  репозиторий, свой бот-аватар. `github_client.py` изолирует работу с API, так
  что миграция затрагивает только слой аутентификации.

## Тесты

`pytest` (моки, без сети): webhook-подпись, маппинг вердиктов, выбор/добавление/
редактирование профилей, резолвинг провайдеров и диспетч протокола, снятие
JSON-обёртки, детект/план/слияние оркестратора, обработчик `/webhook`.
`asyncio_mode = "auto"` — async-тесты без ручных декораторов.
