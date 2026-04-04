# CLAUDE.md

Instructions for Claude Code when working on this repository.

## Quick Reference

```bash
# Run bot locally (из venv, НЕ Docker)
uv run -m app.presentation.telegram

# PostgreSQL работает в Docker контейнере supervisor-db
docker start supervisor-db

# Tests
uv run -m pytest                          # all tests
uv run -m pytest tests/unit tests/e2e -x  # fast subset
uv run -m pytest --cov=app                # with coverage

# Quality
ruff check app tests && ruff format app tests
ty check app tests

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head

# Утилиты
uv run python dump_channel_posts.py       # Дамп постов канала для анализа стиля
uv run python list_channels.py            # Список всех каналов/групп/форумов
```

## Architecture

Multi-agent Telegram platform: moderator bot + assistant bot + Telethon userbot.
Feature-based modular architecture — ORM models as domain models, no entity/interface indirection.
Full details: [`docs/architecture.md`](docs/architecture.md).

### Module Structure

```
app/
├── core/              # Config (9 Pydantic classes), logging, DI container, enums, healthcheck
├── moderation/        # AI moderation: agent, escalation, memory, blacklist, report, services
├── agent/             # AI agent infrastructure (prompts, schemas, tool_trace)
│   └── channel/       # Content pipeline feature module
│       ├── orchestrator.py   # Per-channel scheduling + orchestration
│       ├── workflow.py       # Burr state machine (10 actions, incl. reason_content)
│       ├── brand_voice.py    # Brand Voice Engine: auto-analyze + style profiles
│       ├── translate.py      # Multi-language translation with voice preservation
│       ├── reports.py        # Analytics + competitor intelligence reports
│       ├── notifications.py  # Smart alerts (viral posts, cost spikes)
│       ├── generator.py      # LLM screening + post generation (дегенский стиль)
│       ├── reasoning.py      # Chain-of-thought reasoning (отсеивает новости)
│       ├── analytics.py      # Сбор метрик постов (views, reactions, forwards)
│       ├── best_time.py      # Рекомендация лучшего времени публикации
│       ├── review/           # Review submodule (agent, presentation, service)
│       ├── semantic_dedup.py # pgvector cosine similarity
│       ├── sources.py        # RSS + health tracking
│       ├── external_sources/ # Telegram channels, Twitter/X, Reddit fetchers
│       │   ├── telegram_channels.py  # fetch_channel_posts, fetch_forum_topics, fetch_own_channel_posts
│       │   ├── twitter.py
│       │   └── reddit.py
│       └── http.py           # SSRF-protected HTTP client
├── assistant/         # Conversational admin bot (PydanticAI, 35+ tools, /setup /calendar /healthcheck)
├── infrastructure/    # DB models (SQLAlchemy), repositories, Telethon client
└── presentation/      # Telegram handlers, middlewares, utils (buttons, blacklist)
```

### Key Files

- `app/core/config.py` — Pydantic settings hierarchy (9 nested config classes)
- `app/core/enums.py` — `PostStatus`, `EscalationStatus`, `ReviewDecision` StrEnums
- `app/core/exceptions.py` — `DomainError`, `UserNotFoundException`
- `app/infrastructure/db/models.py` — 9 ORM models (including pgvector `Vector(768)` column)
- `app/core/markdown.py` — `md_to_entities` / `md_to_entities_chunked` (telegramify-markdown)
- `app/core/time.py` — `utc_now()` helper for naive UTC datetimes
- `app/presentation/telegram/bot.py` — main entry, dispatcher setup
- `app/presentation/telegram/handlers/__init__.py` — router assembly, middleware wiring
- `app/core/healthcheck.py` — Startup health checks (DB, Bot API, OpenRouter, Telethon)
- `app/agent/channel/notifications.py` — Smart alerts (viral posts, cost spikes)
- `channel_style_reference.md` — Анализ стиля канала @grassfoundationn

### LLM Models (OpenRouter)

Все модели идут через OpenRouter. Текущая конфигурация экономит токены:

| Role | Model | Env var override |
|---|---|---|
| Screening (batch) | `openai/gpt-4o-mini` | `CHANNEL_SCREENING_MODEL` |
| Reasoning | `openai/gpt-4o-mini` | `CHANNEL_REASONING_MODEL` |
| Generation + review | `openai/gpt-4o-mini` | `CHANNEL_GENERATION_MODEL` |
| Moderation | `google/gemini-3.1-flash-lite-preview` | `MODERATION_MODEL` |
| Assistant | `openai/gpt-4o-mini` | `ASSISTANT_BOT_MODEL` |
| Embeddings | `openai/text-embedding-3-small` | `CHANNEL_EMBEDDING_MODEL` |

### PydanticAI Compatibility

PydanticAI v1.72+ uses `output_type` (NOT `result_type`). All Agent() calls must use `output_type=...`.
Result access: `result.output` (NOT `result.data`).

## Target Channel: @grassfoundationn (Grass forever)

**ID**: `-1001952807891`
**Тематика**: абузы AI-инструментов, крипто-дегенство, полезные девтулы
**НЕ новости**, НЕ аналитика рынка, НЕ пресс-релизы

### Стиль канала (КРИТИЧНО)

- Разговорный, дегенский, как пишешь другу в чат
- НЕ копирайтинг: без буллетпоинтов, без подзаголовков "Почему стоит", "Что внутри"
- Можно лёгкий мат ("ахуеть", "поебаться")
- Обращение на "ты" или "пацаны"
- Личное мнение: "имхо", "кайф", "мне зашло"
- Коротко и по делу, воды ноль
- Концовки: "Лутаем халяву с умом 🤙", "DYOR", "пока лавочку не прикрыли"
- Пошаговые инструкции (1,2,3) ТОЛЬКО для абузов, где нужны конкретные шаги

### Типы контента

1. **Абузы/хаки** — бесплатный доступ к Claude/GPT/Copilot, обход ограничений
2. **Крипто-плейсы** — минты, airdrops, фарминг, результаты
3. **Полезные тулы** — GitHub-репо, сервисы, библиотеки
4. **AI-технологии** — простым языком, "чем полезно лично тебе"

## Content Pipeline Flow

```
fetch_sources (RSS + TG channels + TG forums + Twitter/X + Reddit)
  → split_and_enrich_topics
  → screen_content (batch LLM, threshold=7, + dedup vs own channel posts)
  → reason_content (chain-of-thought, автоскип новостей)
  → generate_post (дегенский стиль, НЕ копирайтинг)
  → send_for_review → HITL halt → publish_post / handle_rejection
```

### Источники контента

- **RSS**: airdropalert.com (decrypt.co и crypto.news УДАЛЕНЫ — генерили новости)
- **Twitter/X**: через Nitter RSS (Фаза 3). Аккаунты: @MONster3638_, @tom_doerr. Фильтрация реплаев и RT.
- **Reddit**: через JSON API (Фаза 5). Сабреддиты: LocalLLaMA, ChatGPT, airdrop, CryptoAirdrop, singularity. Фильтрация: min 10 upvotes, max 24h age, skip stickied/NSFW.
- **Telegram каналы**: 3 канала через Telethon
- **Telegram форумы**: 28 форумов через Telethon (GetForumTopicsRequest из `messages`, НЕ `channels`)
- **Perplexity Sonar discovery**: ОТКЛЮЧЁН (генерил новостной контент, тратил токены)
- **Brave Search**: включён для поиска свежих абузов

### Dedup система

1. **external_id** dedup — по ID в БД (только бот-генерированные посты)
2. **Semantic dedup** — pgvector cosine similarity (threshold 0.85)
3. **Own channel dedup** — fetch_own_channel_posts() через Telethon, сравнение URL и ключевых фраз с последними 30 постами канала (предотвращает повторение ручных постов админа)

## Important Patterns

### parse_mode=None with entities

The moderator bot uses `DefaultBotProperties(parse_mode="HTML")`. This **silently overrides** `entities`/`caption_entities` if `parse_mode=None` is not passed. All `send_photo`/`send_message`/`edit_message` calls using entities MUST include `parse_mode=None`.

### Markdown → Entities

Posts use Markdown (`**bold**`, `[link](url)`) converted via `md_to_entities` from `app/core/markdown.py`. Never send raw Markdown as HTML.

### Telethon Userbot

Session file `moderator_userbot.session`. Provides: chat history, search, member lists, scheduled messages.
**ВАЖНО**: При использовании Telethon channel/forum IDs нужен префикс `-100` (например `1755192276` → `-1001755192276`).
**ВАЖНО**: `GetForumTopicsRequest` находится в `telethon.tl.functions.messages`, НЕ в `channels`.

### Token Optimization

- Batch screening: все items в одном LLM запросе (JSON map index→score)
- Screening threshold поднят до 7 (строже фильтрует)
- Perplexity Sonar отключён
- Новостные RSS удалены
- Reasoning скипает новости автоматически

## Testing

- **600+ tests**, ~20s runtime
- Unit: SQLite in-memory
- Integration: testcontainers PostgreSQL
- E2E: `FakeTelegramServer` (aiohttp-based Bot API simulator)
- Pre-commit: ruff + ty on commit, pytest on push

## Environment

Bot runs in venv (`uv run`), PostgreSQL 17 + pgvector runs in Docker container `supervisor-db`.
See `.env` for all variables. Key ones:

```bash
MODERATOR_BOT_TOKEN=              # Moderator bot
ADMIN_SUPER_ADMINS=459021522      # Admin user ID
OPENROUTER_API_KEY=               # OpenRouter for all LLM calls
BRAVE_API_KEY=                    # Brave Search API
CHANNEL_ENABLED=true
CHANNEL_REASONING_ENABLED=true
CHANNEL_SCREENING_THRESHOLD=7     # Строже чем дефолт (5)
CHANNEL_TEMPERATURE=0.5           # Выше для креативного дегенского стиля
TELETHON_ENABLED=true
DB_HOST=localhost                 # БД в Docker, бот в venv
```

## Known Issues & Fixes Applied

- `external_sources/__init__.py` — `fetch_multiple_channels` переименовано в `fetch_multiple_sources`
- Telethon 1.42: `GetForumTopicsRequest` в `messages`, не `channels`
- Telethon channel IDs: нужен `-100` prefix при передаче в `get_entity()`
- PydanticAI v1.72: `output_type` вместо `result_type`, `result.output` вместо `result.data`
- `extract_usage_from_pydanticai_result(result, model, operation)` — нужны все 3 аргумента
- `log_usage(usage)` — async, принимает LLMUsage, один аргумент
- Windows: `sys.stdout.reconfigure(encoding='utf-8')` для Cyrillic в скриптах
- Footer: `footer_template` в БД должен содержать реальный newline (E'——\n@grassfoundationn')
