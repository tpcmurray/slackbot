# Technical Design Document: Slack Channel Bot

**Version:** 1.0
**Author:** Terry Murray
**Date:** March 29, 2026
**Status:** Draft — ready for vibe coding

---

## 1. Overview

A Python-based Slack bot that lives in `#general`, passively reads all messages, and autonomously decides when to participate in conversation. Backed by a locally-running Qwen 3.5 9B model (vision-capable) via llama.cpp, the bot behaves like "one of the guys" — it can answer questions, trash-talk, and chime in when it has something worth saying.

The bot borrows the OpenClaw workspace-file pattern: behavior is driven by editable markdown/yaml config files (`personality.md`, `heartbeat.md`, `news_summary.yaml`) that can be tweaked without code changes.

### 1.1 Name Candidates

The bot name will be **Kibitz**, Yiddish for an onlooker who offers unsolicited advice. Literally what this bot does.


## 2. Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Local Machine                     │
│                                                     │
│  ┌──────────────┐    ┌──────────────────────────┐   │
│  │  llama.cpp   │    │      Slack Bot (Python)  │   │
│  │  Qwen 3.5 9B │◄───│                          │   │
│  │  + mmproj    │    │  ┌─────────────────────┐ │   │
│  │              │    │  │  Message Buffer     │ │   │
│  │  :8080       │    │  │  (per-channel ring) │ │   │
│  └──────────────┘    │  └─────────────────────┘ │   │
│                      │  ┌─────────────────────┐ │   │
│  ┌──────────────┐    │  │  Heartbeat Scheduler│ │   │
│  │   SearXNG    │◄───│  └─────────────────────┘ │   │
│  │   (Docker)   │    │  ┌─────────────────────┐ │   │
│  │   :8888      │    │  │  News Digest Engine │ │   │
│  └──────────────┘    │  └─────────────────────┘ │   │
│                      └──────────────────────────┘   │
│                                                     │
│  Config Files:                                      │
│  ├── personality.md                                 │
│  ├── heartbeat.md                                   │
│  └── news_summary.yaml                              │
└─────────────────────────────────────────────────────┘
         │
         │ Socket Mode (WebSocket)
         ▼
   ┌───────────┐
   │   Slack    │
   │  #general  │
   └───────────┘
```

### 2.1 Component Summary

| Component | Tech | Port | Purpose |
|-----------|------|------|---------|
| Slack Bot | Python 3.11+, `slack_bolt` | — | Core bot process. Socket Mode (WebSocket, no public URL needed). |
| LLM Inference | llama.cpp server | 8080 | Qwen 3.5 9B with vision projector. OpenAI-compatible API. |
| Web Search | SearXNG (Docker) | 8888 | Self-hosted meta search engine for RAG lookups. |
| Config Files | Markdown / YAML | — | Editable behavior, personality, heartbeat tasks, news feeds. |

---

## 3. Slack App Setup

### 3.1 Create Slack App

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**
2. Name it whatever the bot name ends up being
3. Select the workspace (Terry is admin)

### 3.2 Socket Mode

1. **Settings → Socket Mode** → Enable
2. Generate an **App-Level Token** with `connections:write` scope
3. Save as `SLACK_APP_TOKEN`

### 3.3 Bot Token Scopes (OAuth & Permissions)

| Scope | Why |
|-------|-----|
| `channels:history` | Read messages in public channels |
| `channels:read` | List channels, get channel info |
| `chat:write` | Post messages |
| `files:read` | Download images users post |
| `files:write` | Upload images if needed in future |
| `reactions:read` | See emoji reactions (future use) |
| `users:read` | Resolve usernames for personality |

### 3.4 Event Subscriptions

Subscribe to these bot events:

| Event | Why |
|-------|-----|
| `message.channels` | Every message in public channels the bot is in |

### 3.5 Install & Tokens

1. Install app to workspace
2. Copy **Bot User OAuth Token** → `SLACK_BOT_TOKEN`
3. Invite bot to `#general`: `/invite @botname`

### 3.6 Environment Variables

```bash
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
LLAMA_CPP_URL=http://localhost:8080
SEARXNG_URL=http://localhost:8888
BOT_NAME=kibitz  # or whatever name is chosen
CHANNEL_NAME=general
```

---

## 4. llama.cpp Setup

### 4.1 Model

- **Model:** `Jackrong/Qwen3.5-9B-Claude-4.6-Opus-Reasoning-Distilled-v2` (GGUF)
- **Vision projector:** Required for image understanding. Must match the model's architecture.

### 4.2 Server Launch

```bash
llama-server \
  -m Qwen3.5-9B.Q8_0.gguf \
  --mmproj mmproj-BF16.gguf \
  --port 8080 \
  --host 127.0.0.1 \
  -c 8192 \
  -ngl 99
```

> **Note:** Confirm the mmproj file availability for this specific model. If one isn't published, it may need to be extracted or a compatible one sourced.

### 4.3 API Compatibility

llama.cpp exposes an OpenAI-compatible endpoint:

```
POST http://localhost:8080/v1/chat/completions
```

Standard `messages` array with `role`/`content`. Vision inputs use the OpenAI multimodal format:

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."} },
        {"type": "text", "text": "What is this?"}
      ]
    }
  ]
}
```

---

## 5. SearXNG Setup

### 5.1 Docker Compose

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    ports:
      - "8888:8080"
    volumes:
      - ./searxng:/etc/searxng
    environment:
      - SEARXNG_BASE_URL=http://localhost:8888/
    restart: unless-stopped
```

### 5.2 Configuration

In `./searxng/settings.yml`, enable JSON API:

```yaml
search:
  formats:
    - html
    - json
server:
  limiter: false  # local only, no rate limiting needed
```

### 5.3 API Usage

```
GET http://localhost:8888/search?q=GDP+of+Canada&format=json
```

Returns structured results with `title`, `url`, `content` (snippet) fields.

---

## 6. Message Processing Pipeline

### 6.1 Message Buffer

A rolling in-memory buffer per channel. Stores the last N messages for context.

```python
@dataclass
class BufferedMessage:
    timestamp: str
    user_id: str
    username: str
    text: str
    has_image: bool
    image_urls: list[str]  # Slack file URLs
    thread_ts: str | None

class MessageBuffer:
    def __init__(self, max_size: int = 50):
        self.messages: deque[BufferedMessage] = deque(maxlen=max_size)

    def add(self, msg: BufferedMessage) -> None: ...
    def recent(self, n: int = 10) -> list[BufferedMessage]: ...
    def full_context(self) -> list[BufferedMessage]: ...
```

### 6.2 Two-Tier Response Decision

Every incoming message goes through a two-tier evaluation:

#### Tier 1: Quick Triage (cheap, fast)

Runs on every message. Uses minimal context (last ~10 messages). The LLM answers a structured question:

**System prompt for triage:**
```
You are the triage module for a Slack bot named {BOT_NAME}.
Given the recent conversation, decide if the bot should respond.

Rules:
- ALWAYS respond if someone mentions "{BOT_NAME}" by name
- ALWAYS respond if someone asks a direct question and it seems directed at the bot
- MAYBE respond if there's an opportunity for humor, trash talk, or a useful contribution
- NEVER respond to mundane chatter, acknowledgments, or messages clearly between other people
- Respect the cooldown: the bot last spoke {seconds_ago} seconds ago. If < {cooldown_seconds}, only respond if directly addressed by name.

Reply with JSON only:
{"should_respond": true/false, "reason": "brief explanation", "needs_search": false, "is_image_question": false}
```

> **Cooldown default:** 300 seconds (5 minutes) for autonomous responses. No cooldown when addressed by name.

#### Tier 2: Response Generation (full context)

Only runs if Tier 1 returns `should_respond: true`. Uses the full message buffer (~50 messages) plus `personality.md` as system prompt.

### 6.3 Two-Phase Response Pattern

When the bot is asked a factual question (especially when `needs_search: true` from triage):

**Phase 1 — Quick Acknowledgment:**
The bot immediately posts a short gut-reaction response based on model knowledge alone. This confirms it heard the question and is working on it.

Example: *"Somewhere in the $2.4 trillion range, but let me get the exact number..."*

**Phase 2 — Researched Answer:**
The bot queries SearXNG, feeds top results into the LLM, and posts a follow-up with the real answer.

Example: *"Canada's GDP was $2.14 trillion USD in 2024 according to the World Bank."*

**Implementation:**

```python
async def handle_factual_question(message, context):
    # Phase 1: Quick response
    quick_prompt = build_quick_response_prompt(message, context.recent(10))
    quick_answer = await llm_generate(quick_prompt)
    await post_to_slack(channel, quick_answer)

    # Phase 2: Search + researched response
    search_results = await searxng_query(extract_search_query(message))
    researched_prompt = build_researched_prompt(message, search_results)
    full_answer = await llm_generate(researched_prompt)
    await post_to_slack(channel, full_answer)
```

### 6.4 Vision Pipeline

When a message includes an image and the bot decides to respond:

1. **Detect image:** Check Slack event for `files` array with `mimetype` starting with `image/`
2. **Download:** Use `SLACK_BOT_TOKEN` to fetch from `file.url_private_download`
3. **Encode:** Base64-encode the image bytes
4. **Send to LLM:** Include as multimodal content in the chat completion request
5. **Respond:** Post the model's analysis to the channel

```python
async def download_slack_image(file_info: dict) -> bytes:
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        async with session.get(file_info["url_private_download"], headers=headers) as resp:
            return await resp.read()
```

---

## 7. Config Files

### 7.1 personality.md

Defines the bot's character. Loaded as the system prompt for Tier 2 response generation.

```markdown
# {BOT_NAME}

You are {BOT_NAME}, a member of a Slack channel with three brothers: Terry, Tom, and Nick.

## Core Personality
- You're one of the guys. You give them shit, they give you shit. It's all love.
- You're witty, sarcastic, and occasionally helpful despite yourself.
- You don't suck up or act like a corporate assistant. You're a friend, not a service.
- If someone says something dumb, you call it out. Gently. Mostly.

## Behavioral Guidelines
- Keep responses short and punchy. This is Slack, not an essay contest.
- Use lowercase naturally. Don't over-capitalize.
- You can use emoji sparingly. Don't be cringe about it.
- If you don't know something, say so. Don't bullshit.
- You can reference previous conversation in the channel. You've been reading.

## Things You're Into
- Tech, AI/LLM stuff (you're nerdy about it)
- Gaming (the brothers play together Thursday nights)
- Giving unsolicited opinions

## Things You Don't Do
- Never be genuinely mean or hurtful
- Never touch serious/sensitive topics with humor
- Don't spam the channel. Quality over quantity.
- Don't use phrases like "as an AI" or "I'm just a bot"
```

> **This file is the primary tuning surface.** Edit it freely to adjust personality over time without touching code.

### 7.2 heartbeat.md

Defines periodic autonomous behaviors. Read by the heartbeat scheduler.

```markdown
# Heartbeat Tasks

## News Digest
- Schedule: Daily at 09:00 NST
- Action: Run news digest from news_summary.yaml
- Target: #general

## Quiet Channel Check
- Schedule: Every 4 hours during 10:00-22:00 NST
- Condition: No messages in #general for > 3 hours
- Action: Post a conversation starter, observation, or light roast
- Personality: Use personality.md tone
```

> **Future tasks** can be added here as new sections. The heartbeat runner parses this file and dispatches accordingly.

### 7.3 news_summary.yaml

Defines news digest topics, feeds, and relevance criteria.

```yaml
digests:
  - title: "AI/LLM News"
    feeds:
      - https://feeds.arstechnica.com/arstechnica/ai
      - https://www.theverge.com/rss/ai-artificial-intelligence/index.xml
      - https://blog.google/technology/ai/rss/
      - https://openai.com/blog/rss.xml
      - https://www.anthropic.com/feed
      - https://huggingface.co/blog/feed.xml
      - https://simonwillison.net/atom/everything/
    relevance: |
      I care most about:
      - New model releases and benchmarks
      - Open source LLM developments (especially quantization, local inference, small models)
      - AI coding tools and developer productivity
      - Context engineering, RAG, and retrieval techniques
      - Anything llama.cpp, vLLM, or local inference related
      I care less about:
      - AI ethics opinion pieces without substance
      - Enterprise SaaS AI product launches
      - Regulatory news unless it's a major policy shift
    max_articles: 5
    post_time: "09:00"
    timezone: "America/St_Johns"

  # Example: add more digests by copying the block above
  # - title: "Gaming News"
  #   feeds: [...]
  #   relevance: |
  #     ...
```

---

## 8. News Digest Engine

### 8.1 Flow

```
[Scheduled at post_time] → For each digest in news_summary.yaml:
  1. Fetch all RSS feeds → collect articles from last 24 hours
  2. Deduplicate by URL and similar titles
  3. Build a prompt with article titles/summaries + relevance criteria
  4. Ask LLM to rank top {max_articles} by relevance and generate short descriptions
  5. Post to #general:
     a. Parent message: "{title}, {Month Day, Year}"
     b. 5 threaded replies, each: short description + link
```

### 8.2 Thread Structure

```
┌──────────────────────────────────────────────────┐
│ 🗞️ AI/LLM News, March 30, 2026                  │  ← parent message
├──────────────────────────────────────────────────┤
│ Thread replies:                                   │
│                                                   │
│ 1. Qwen 3.5 hits top of open-source benchmarks   │
│    with a 9B parameter model that punches way     │
│    above its weight. https://example.com/...      │
│                                                   │
│ 2. llama.cpp adds native speculative decoding     │
│    support, 2-3x throughput on consumer GPUs.     │
│    https://example.com/...                        │
│                                                   │
│ ... (3 more)                                      │
└──────────────────────────────────────────────────┘
```

> **Note:** The news digest is the one place where threads ARE used, since dumping 5 articles into the main channel flow would be obnoxious.

### 8.3 RSS Parsing

Use `feedparser` library. Filter articles by `published_parsed` or `updated_parsed` within the last 24 hours. Fall back to entry order if dates are missing.

---

## 9. Heartbeat Scheduler

### 9.1 Implementation

A background `asyncio` task that runs on a 1-minute tick. Each tick, it checks `heartbeat.md` for tasks whose schedule condition is met.

```python
class HeartbeatScheduler:
    def __init__(self, heartbeat_path: str, bot: SlackBot):
        self.heartbeat_path = heartbeat_path
        self.bot = bot
        self.last_runs: dict[str, datetime] = {}

    async def run_loop(self):
        while True:
            tasks = parse_heartbeat_file(self.heartbeat_path)
            now = datetime.now(ZoneInfo("America/St_Johns"))

            for task in tasks:
                if self.should_run(task, now):
                    await self.execute(task)
                    self.last_runs[task.name] = now

            await asyncio.sleep(60)
```

### 9.2 Heartbeat File Reloading

The heartbeat file is re-read on every tick (it's tiny, this is fine). This means you can edit `heartbeat.md` while the bot is running and changes take effect within 60 seconds.

### 9.3 Timezone

All schedule times are in `America/St_Johns` (NST/NDT). Use `zoneinfo.ZoneInfo` — no `pytz`.

---

## 10. Project Structure

```
slackbot/
├── bot.py                  # Entry point, Slack app init, event handlers
├── config.py               # Load env vars, paths, constants
├── llm.py                  # LLM client (chat completions, vision)
├── triage.py               # Tier 1 should-respond logic
├── responder.py            # Tier 2 response generation (personality-driven)
├── search.py               # SearXNG client + two-phase response orchestration
├── vision.py               # Image download from Slack + base64 encoding
├── buffer.py               # MessageBuffer (rolling in-memory message store)
├── heartbeat.py            # Heartbeat scheduler + task parser
├── news.py                 # RSS fetching, dedup, LLM ranking, Slack posting
├── config/
│   ├── personality.md      # Bot personality (system prompt)
│   ├── heartbeat.md        # Scheduled autonomous tasks
│   └── news_summary.yaml   # News digest definitions
├── docker-compose.yml      # SearXNG container
├── requirements.txt        # Python dependencies
├── .env                    # Tokens and config (not committed)
├── .env.example            # Template for .env
└── README.md               # Setup instructions
```

---

## 11. Dependencies

### 11.1 Python Packages

```
slack-bolt>=1.18.0
slack-sdk>=3.27.0
aiohttp>=3.9.0
feedparser>=6.0.0
pyyaml>=6.0.0
python-dotenv>=1.0.0
```

### 11.2 External Services

| Service | How to get it |
|---------|---------------|
| llama.cpp | Build from source or use pre-built binary. Must support `--mmproj`. |
| SearXNG | `docker compose up -d` from project root. |

### 11.3 Slack App

Created manually via https://api.slack.com/apps. See Section 3 for full setup.

---

## 12. Startup Sequence

```bash
# 1. Start llama.cpp (separate terminal or background)
llama-server -m model.gguf --mmproj mmproj.gguf --port 8080 -c 8192 -ngl 99

# 2. Start SearXNG
cd slackbot && docker compose up -d

# 3. Start the bot
cd slackbot && python bot.py
```

### 12.1 Bot Startup Checks

On startup, `bot.py` should verify:

1. llama.cpp is reachable at `LLAMA_CPP_URL` (hit `/health` or `/v1/models`)
2. SearXNG is reachable at `SEARXNG_URL` (hit `/search?q=test&format=json`)
3. Slack tokens are valid (Bolt SDK handles this on connect)
4. Config files exist and parse correctly
5. Bot is a member of the target channel

If any check fails, log the error clearly and exit. Don't silently run in a degraded state.

---

## 13. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Socket Mode over HTTP | No public URL needed. Perfect for local machine. |
| Two-tier triage | Keeps inference cheap for the 90% of messages the bot ignores. |
| Two-phase factual response | Makes the bot feel human — quick acknowledgment, then real answer. |
| File-based config over DB | Easy to edit, version control friendly, no infrastructure. OpenClaw pattern. |
| In-memory message buffer | Simple, fast, no persistence needed. Buffer rebuilds on restart from Slack history if needed. |
| SearXNG over commercial API | Self-hosted, free, no API keys, fits local-first philosophy. |
| Single channel scope | Keeps v1 simple. Multi-channel is a config change, not an architecture change. |
| asyncio throughout | Slack Bolt async + aiohttp for non-blocking LLM/search calls. |

---

## 14. Future Considerations (Not in v1)

These are explicitly out of scope for initial build but the architecture should not prevent them:

- **Multi-channel support:** Buffer and triage are already per-channel conceptually
- **DM support:** Add `message.im` event subscription
- **Slash commands:** `/botname ask ...` for explicit queries
- **Emoji reactions:** React to messages instead of/in addition to replying
- **Image generation:** If a multimodal output model is added later
- **Persistent memory:** Vector store for long-term conversation memory across restarts
- **Additional heartbeat tasks:** The file-based system is extensible by design
- **Multiple LLM backends:** Swap `LLAMA_CPP_URL` or add model routing

---

## 15. Open Questions

1. **Bot name:** Pick from candidates in Section 1.1 or propose a new one.
2. **Vision projector:** Confirm mmproj file availability for `Qwen3.5-9B-Claude-4.6-Opus-Reasoning-Distilled-v2`. If unavailable, document fallback plan.
3. **Cooldown tuning:** Starting at 5 minutes for autonomous responses. May need adjustment based on channel activity.
4. **News feed curation:** The example feeds in `news_summary.yaml` are starting points. Refine after first few runs.
5. **Quiet channel behavior:** The "conversation starter when quiet" heartbeat task needs personality guardrails so it doesn't feel forced.
