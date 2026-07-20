# AI PR Reviewer

AI-код-ревьюер для GitHub pull request'ов. Берёт diff PR, прогоняет через LLM и
оставляет **review с вердиктом** — `APPROVE` / `REQUEST_CHANGES` / `COMMENT` —
с комментариями прямо на строках кода.

По умолчанию заточен под **Android**, но фокус ревью — это **профиль**, который
настраивается под любое направление (backend, web, KMP, безопасность — что
угодно). Работает тремя способами: авто-ревью на каждый PR через **GitHub
Actions**, разовое ревью **из терминала** и как **webhook-сервис**.

## Возможности

- ✅ **Вердикт + inline-комментарии** — замечания вешаются на конкретные строки diff.
- 🔁 **Любая LLM** — DeepSeek, OpenAI, Claude или **локальная модель** (Ollama /
  LM Studio / vLLM, без ключа и без облака). Переключается одной командой.
- 🎯 **Профили ревью под любое направление** — из коробки Android (`android`,
  `compose`, `kmp`); **свои профили** под что угодно добавляются без правки кода.
- 🤖 **Авто-режим `auto`** — сам определяет, про что PR, и запускает подходящие
  профили, даже несколько сразу.
- 🖥️ **Дружелюбный CLI** — мастер настройки со скрытым вводом ключа, `doctor`,
  разовое ревью, управление профилями и провайдерами.

## Установка

### Вариант 1 — как утилита (pipx): одна команда, глобально

```bash
pipx install git+https://github.com/Sermage/pr-reviewer
```

Ставит команду **`pr-reviewer`** в систему — без клонирования и возни с venv.
Конфиг и свои профили живут в `~/.pr-reviewer/`. Дальше сразу `pr-reviewer setup`.
Идеально для ревью из терминала. (Нет pipx — `brew install pipx`.)

### Вариант 2 — как репозиторий: для webhook-сервиса, GitHub Actions и разработки

```bash
git clone https://github.com/Sermage/pr-reviewer
cd pr-reviewer
./install.sh
```

`install.sh` создаёт локальное окружение, ставит зависимости и сразу предлагает
настройку. Здесь `.env`, свои профили и workflow лежат в репозитории (едут в
Actions). Он также кладёт симлинк в `~/.local/bin`, так что команда **`pr-reviewer`**
доступна глобально (если `~/.local/bin` в `PATH` — install.sh подскажет, как
добавить). Прямо из папки репозитория всегда работает лаунчер **`./pr-reviewer
<команда>`** без активации venv.

## Быстрый старт

Мастер проведёт по шагам: окружение → провайдер LLM → ключ (вводится скрыто) →
профиль → GitHub Actions.

```bash
pr-reviewer setup    # интерактивная настройка
pr-reviewer doctor   # проверить, что всё на месте
```

## Использование

> При установке через **pipx** команда глобальная — пиши `pr-reviewer` как есть.
> В варианте с **репозиторием** без активации venv добавляй `./`:
> `./pr-reviewer <команда>`.

```bash
pr-reviewer setup                     # интерактивная настройка
pr-reviewer doctor                    # проверить настройку
pr-reviewer review --pr 1 --dry-run   # разовое ревью PR (показать, не постить)
pr-reviewer review --repo o/n --pr 1  # ревью и публикация в PR
pr-reviewer profile                   # профили ревью
pr-reviewer provider                  # LLM-провайдеры
pr-reviewer serve                     # webhook-сервис локально
pr-reviewer help                      # все команды
```

`review` берёт токен из `GITHUB_TOKEN` или `gh auth token`, ключ и настройки —
из `.env`. Флаги: `--profile <имя>`, `--dry-run`, `--approve`.

### Провайдер LLM

```bash
pr-reviewer provider                 # список + активный
pr-reviewer provider claude          # Claude API
pr-reviewer provider openai --model gpt-4o
pr-reviewer provider local           # локальная модель (Ollama по умолчанию)
```

| Провайдер | Модель по умолчанию | Ключ |
|---|---|---|
| `deepseek` | `deepseek-chat` | нужен |
| `openai` | `gpt-4o-mini` | нужен |
| `claude` | `claude-sonnet-5` | нужен |
| `local` | `qwen2.5-coder` (Ollama) | не нужен |

Локальная модель целиком офлайн:

```bash
ollama pull qwen2.5-coder
pr-reviewer provider local
pr-reviewer review --pr 1 --dry-run
```

### Профили ревью

```bash
pr-reviewer profile                  # список + активный
pr-reviewer profile compose          # переключить фокус
pr-reviewer profile auto             # авто-режим (оркестратор)
```

Свой профиль — без правки кода:

```bash
pr-reviewer profile --add security --from security.md   # из файла
pr-reviewer profile --add gradle --focus "Проверяй version catalogs, ..."
pr-reviewer profile --show security   # посмотреть, что проверяет
pr-reviewer profile --edit security   # отредактировать ($EDITOR)
pr-reviewer profile --remove security
```

### Авто-режим (`auto`)

`pr-reviewer profile auto` включает оркестратор: он сам смотрит на diff,
определяет направления PR и запускает подходящие профили (несколько при
необходимости). Если для найденного направления профиля нет — использует
дефолтный и честно пишет в заключении, что точность по этому направлению снижена
и какой профиль стоит добавить.

## GitHub Actions (авто-ревью на каждый PR)

Мастер `pr-reviewer setup` предложит настроить всё сам (если `gh` авторизован):
поставит секрет с ключом и переменные провайдера/профиля. Workflow уже лежит в
[`.github/workflows/ai-review.yml`](.github/workflows/ai-review.yml) — ревью
запускается на `opened` / `synchronize` / `reopened`.

Вручную: секрет `LLM_API_KEY` и (опционально) variables `REVIEW_PROFILE`,
`LLM_PROVIDER`, `LLM_MODEL`.

## Webhook-сервис

```bash
cp .env.example .env        # впиши ключи
pr-reviewer serve           # http://localhost:8000
```

GitHub не достучится до `localhost` — пробрось туннель и укажи URL в
**Settings → Webhooks** (Payload = `<url>/webhook`, Content type =
`application/json`, Secret = `WEBHOOK_SECRET`, событие = **Pull requests**):

```bash
npx smee-client --url https://smee.io/<channel> --target http://localhost:8000/webhook
# или: ngrok http 8000
```

## Демо без GitHub

Прогнать конвейер на сохранённом diff (удобно для показа):

```bash
LLM_API_KEY=sk-... python scripts/demo_local.py samples/sample.diff
python scripts/demo_local.py samples/sample.diff --profile auto
```

## Тесты

```bash
pytest
```

---

Как устроено внутри — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
