# Phases & Tasks Breakdown: Slack Channel Bot

**Purpose:** Step-by-step build plan for agentic/vibe coding with Claude Opus 4.6
**Source:** `slackbot-tdd.md` v1.0
**Date:** March 29, 2026

---

## How to Use This Document

Each phase is a self-contained coding session. Hand the phase (with its tasks) to the agent along with the TDD for reference. Each task has:

- **Do:** What to build
- **Accept when:** How the agent (and you) know it's done
- **Files:** What gets created or modified
- **Context:** Any TDD sections the agent needs to reference

Phases are sequential — each builds on the previous. Tasks within a phase can often be done in one shot.

---

## Phase 0: Project Skeleton & Config

**Goal:** Scaffold the project structure, config loading, env vars, and all three workspace config files so every subsequent phase has a foundation to build on.

### Task 0.1 — Project scaffold
- **Do:** Create the full directory structure from TDD Section 10. Create `requirements.txt` (Section 11.1), `.env.example` (Section 3.6), `.gitignore` (exclude `.env`, `__pycache__`, `*.pyc`). Create empty `__init__.py` files as needed.
- **Accept when:** `pip install -r requirements.txt` succeeds. Directory structure matches TDD Section 10.
- **Files:** All directories, `requirements.txt`, `.env.example`, `.gitignore`

### Task 0.2 — Config loader
- **Do:** Implement `config.py` — load env vars from `.env` via `python-dotenv`, expose constants (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `LLAMA_CPP_URL`, `SEARXNG_URL`, `BOT_NAME`, `CHANNEL_NAME`), and paths to the three config files. Fail loudly if required env vars are missing.
- **Accept when:** `from config import *` works. Missing env vars raise clear errors with the var name.
- **Files:** `slackbot/config.py`
- **Context:** TDD Section 3.6, 10

### Task 0.3 — Workspace config files
- **Do:** Create `personality.md`, `heartbeat.md`, and `news_summary.yaml` in `slackbot/config/` using the exact content from TDD Sections 7.1, 7.2, 7.3. Use `BOT_NAME` placeholder `{BOT_NAME}` where the TDD does.
- **Accept when:** All three files exist with correct content. `news_summary.yaml` parses cleanly with PyYAML.
- **Files:** `slackbot/config/personality.md`, `slackbot/config/heartbeat.md`, `slackbot/config/news_summary.yaml`
- **Context:** TDD Section 7

---

## Phase 1: LLM Client

**Goal:** A working async client for the llama.cpp OpenAI-compatible API, including vision support.

### Task 1.1 — Text chat completions
- **Do:** Implement `llm.py` with an async function that sends a `messages` array to `POST {LLAMA_CPP_URL}/v1/chat/completions` via `aiohttp` and returns the response text. Handle connection errors and non-200 status codes with clear logging. Include a health check function that hits `/health` or `/v1/models`.
- **Accept when:** With llama.cpp running, calling the function with a simple prompt returns a string response. Health check returns True/False correctly.
- **Files:** `slackbot/llm.py`
- **Context:** TDD Section 4.3

### Task 1.2 — Vision support
- **Do:** Extend `llm.py` to accept optional base64 image data. When provided, format the message using the OpenAI multimodal content array (image_url + text). Implement `vision.py` with a function to download an image from a Slack private URL using the bot token and return base64-encoded bytes.
- **Accept when:** Can send a base64 image + text prompt to llama.cpp and get a description back. `download_slack_image` correctly adds the Authorization header.
- **Files:** `slackbot/llm.py`, `slackbot/vision.py`
- **Context:** TDD Section 4.3, 6.4

---

## Phase 2: Message Buffer

**Goal:** In-memory rolling message store with the `BufferedMessage` dataclass.

### Task 2.1 — Buffer implementation
- **Do:** Implement `buffer.py` exactly as described in TDD Section 6.1. `BufferedMessage` dataclass with all specified fields. `MessageBuffer` class using `collections.deque(maxlen=50)` with `add()`, `recent(n)`, and `full_context()` methods.
- **Accept when:** Can create a buffer, add messages, retrieve recent N, and verify maxlen eviction works. A quick manual test or inline `if __name__ == "__main__"` block is sufficient.
- **Files:** `slackbot/buffer.py`
- **Context:** TDD Section 6.1

---

## Phase 3: Triage (Tier 1)

**Goal:** The fast, cheap "should I respond?" decision engine.

### Task 3.1 — Triage module
- **Do:** Implement `triage.py`. Given the recent message buffer (last ~10 messages), bot name, and seconds since the bot last spoke, build the triage system prompt from TDD Section 6.2 and call the LLM. Parse the JSON response (`should_respond`, `reason`, `needs_search`, `is_image_question`). Implement cooldown logic: 300s default for autonomous responses, no cooldown when addressed by name. Handle malformed JSON gracefully (default to not responding).
- **Accept when:** Given a buffer containing "hey {BOT_NAME} what's up", returns `should_respond: true`. Given mundane chatter with recent bot activity, returns `should_respond: false`. Malformed LLM output doesn't crash.
- **Files:** `slackbot/triage.py`
- **Context:** TDD Section 6.2

---

## Phase 4: Response Generation (Tier 2)

**Goal:** Personality-driven response generation using the full buffer context.

### Task 4.1 — Responder module
- **Do:** Implement `responder.py`. Load `personality.md` as the system prompt (re-read on each call so edits take effect live). Build the user message from the full buffer context (~50 messages) formatted as a conversation log. Call the LLM and return the response text. Replace `{BOT_NAME}` in the personality file with the actual bot name from config.
- **Accept when:** Given a personality file and a buffer with conversation, returns an in-character response. Editing `personality.md` changes the response style without restart.
- **Files:** `slackbot/responder.py`
- **Context:** TDD Section 6.2 (Tier 2), 7.1

---

## Phase 5: SearXNG Client & Two-Phase Responses

**Goal:** Web search integration and the quick-ack-then-researched-answer pattern.

### Task 5.1 — SearXNG client
- **Do:** Implement `search.py` with an async function that queries `{SEARXNG_URL}/search?q={query}&format=json` via `aiohttp`, extracts `title`, `url`, and `content` from results, and returns a list of result dicts. Include a health check function. Handle connection errors gracefully.
- **Accept when:** With SearXNG running, querying "GDP of Canada" returns structured results with title/url/content fields.
- **Files:** `slackbot/search.py`
- **Context:** TDD Section 5.3

### Task 5.2 — Two-phase response orchestration
- **Do:** In `search.py` (or `responder.py` — use your judgment), implement the two-phase factual response pattern from TDD Section 6.3. Phase 1: quick gut-reaction answer using only model knowledge. Phase 2: query SearXNG, feed top results into a new LLM call, post the researched answer. Both phases need a Slack posting callback (accept a callable or coroutine for posting — the actual Slack client comes in Phase 6).
- **Accept when:** The function calls LLM twice (quick then researched) and invokes the posting callback twice with distinct messages. The researched prompt includes search result content.
- **Files:** `slackbot/search.py` or `slackbot/responder.py`
- **Context:** TDD Section 6.3

---

## Phase 6: Slack Bot Core (Wire It All Together)

**Goal:** The main `bot.py` entry point that connects Slack events to the processing pipeline.

### Task 6.1 — Slack app init and event handling
- **Do:** Implement `bot.py` using `slack_bolt.async_app.AsyncApp` in Socket Mode. Subscribe to `message.channels` events. On each message:
  1. Resolve the username from `user_id` (via `users:read` / Slack client)
  2. Build a `BufferedMessage` and add to the channel's `MessageBuffer`
  3. Detect images (check event for `files` with image mimetypes)
  4. Run triage (Tier 1) on the buffer
  5. If `should_respond`: run responder (Tier 2) or two-phase search depending on triage result
  6. If `is_image_question` and image present: download image, run vision LLM call
  7. Post response to channel
- **Accept when:** Bot connects to Slack, receives messages, buffers them, and responds when triage says yes. Direct mentions always get a response. Mundane chatter is ignored.
- **Files:** `slackbot/bot.py`
- **Context:** TDD Sections 3, 6, 12

### Task 6.2 — Startup health checks
- **Do:** Add startup verification to `bot.py` per TDD Section 12.1: check llama.cpp reachability, SearXNG reachability, config file existence/parsing, and that the bot is in the target channel. Log clear errors and exit on failure — no silent degraded state.
- **Accept when:** Bot refuses to start with a clear error if llama.cpp is down, SearXNG is down, config files are missing, or bot isn't in the channel.
- **Files:** `slackbot/bot.py`
- **Context:** TDD Section 12.1

---

## Phase 7: News Digest Engine

**Goal:** RSS-based news digest with LLM ranking, posted as threaded Slack messages.

### Task 7.1 — RSS fetcher and deduplication
- **Do:** Implement the RSS portion of `news.py`. Parse `news_summary.yaml`, fetch all feeds for each digest using `feedparser`, filter to articles from the last 24 hours (by `published_parsed` or `updated_parsed`, fall back to entry order), and deduplicate by URL and similar titles.
- **Accept when:** Given the example `news_summary.yaml`, fetching returns a deduplicated list of recent articles with title, url, and summary/content fields.
- **Files:** `slackbot/news.py`
- **Context:** TDD Section 8.1, 8.3, 7.3

### Task 7.2 — LLM ranking and Slack posting
- **Do:** Complete `news.py`. Take the fetched articles + the digest's `relevance` criteria, build an LLM prompt that asks for the top N articles ranked by relevance with short descriptions. Post to Slack as a parent message (digest title + date) with individual articles as threaded replies (description + link). Match the thread structure from TDD Section 8.2.
- **Accept when:** Running the digest posts a parent message to #general with 5 threaded replies, each containing a short description and link.
- **Files:** `slackbot/news.py`
- **Context:** TDD Section 8.1, 8.2

---

## Phase 8: Heartbeat Scheduler

**Goal:** Background async loop that parses `heartbeat.md` and triggers scheduled tasks.

### Task 8.1 — Heartbeat parser and scheduler loop
- **Do:** Implement `heartbeat.py` with the `HeartbeatScheduler` class from TDD Section 9.1. Parse `heartbeat.md` on every tick (re-read the file each 60s loop). Support the two task types from the default heartbeat file:
  1. **News Digest:** Trigger at scheduled time (parse "Daily at HH:MM NST" format), call the news digest engine from Phase 7
  2. **Quiet Channel Check:** Trigger on interval during time window (parse "Every N hours during HH:MM-HH:MM NST"), check buffer for inactivity condition, generate a conversation starter via LLM with personality prompt
- Use `zoneinfo.ZoneInfo("America/St_Johns")` for all time handling — no `pytz`.
- **Accept when:** Scheduler runs as a background asyncio task. News digest fires at configured time. Quiet channel check fires when conditions are met. Editing `heartbeat.md` takes effect within 60 seconds.
- **Files:** `slackbot/heartbeat.py`
- **Context:** TDD Section 9

### Task 8.2 — Integrate heartbeat into bot.py
- **Do:** Start the `HeartbeatScheduler` as a background task in `bot.py`'s startup. Pass it the Slack client and message buffer so it can post messages and check channel activity.
- **Accept when:** Bot starts with heartbeat running in background. Heartbeat tasks execute on schedule without blocking message handling.
- **Files:** `slackbot/bot.py`
- **Context:** TDD Section 9, 12

---

## Phase 9: Docker Compose & Docs

**Goal:** SearXNG docker-compose file and setup README.

### Task 9.1 — Docker Compose for SearXNG
- **Do:** Create `docker-compose.yml` in the project root per TDD Section 5.1. Create `searxng/settings.yml` with JSON API enabled and limiter disabled per TDD Section 5.2.
- **Accept when:** `docker compose up -d` starts SearXNG on port 8888 and `curl http://localhost:8888/search?q=test&format=json` returns results.
- **Files:** `slackbot/docker-compose.yml`, `slackbot/searxng/settings.yml`
- **Context:** TDD Section 5

### Task 9.2 — README
- **Do:** Create `README.md` with: prerequisites (Python 3.11+, Docker, llama.cpp), setup steps (env vars, SearXNG, llama.cpp, bot), and the startup sequence from TDD Section 12.
- **Accept when:** A new user can follow the README to get the bot running.
- **Files:** `slackbot/README.md`
- **Context:** TDD Section 12

---

## Phase Summary

| Phase | What | Key Files | Depends On |
|-------|------|-----------|------------|
| 0 | Skeleton & config | config.py, config files, requirements.txt | Nothing |
| 1 | LLM client | llm.py, vision.py | Phase 0 |
| 2 | Message buffer | buffer.py | Phase 0 |
| 3 | Triage (Tier 1) | triage.py | Phase 1, 2 |
| 4 | Responder (Tier 2) | responder.py | Phase 1, 2 |
| 5 | Search + two-phase | search.py | Phase 1 |
| 6 | Slack bot core | bot.py | Phase 2, 3, 4, 5 |
| 7 | News digest | news.py | Phase 1 |
| 8 | Heartbeat scheduler | heartbeat.py | Phase 6, 7 |
| 9 | Docker + docs | docker-compose.yml, README.md | All |

---

## Agent Instructions

When handing a phase to the coding agent, include:

1. This phase's tasks (copy the relevant section)
2. The full TDD (`slackbot-tdd.md`) as reference
3. The instruction: *"Implement this phase. Read the TDD sections referenced in each task. Build exactly what's specified — no extras, no skipped fields. Test what you can test locally."*

For phases that modify existing files (Phase 6.2, 8.2), also include the current file content so the agent has the full picture.
