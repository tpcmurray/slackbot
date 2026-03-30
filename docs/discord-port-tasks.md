# Discord Port — Tasks Breakdown

**Purpose:** Port Kibitz from Slack to Discord
**Date:** March 29, 2026

---

## What Changes, What Doesn't

### Stays the same (no changes needed)
- `llm.py` — LLM client is transport-agnostic
- `buffer.py` — BufferedMessage dataclass and MessageBuffer work as-is
- `triage.py` — triage logic doesn't know about Slack
- `responder.py` — personality-driven generation is transport-agnostic
- `search.py` — SearXNG client and two-phase logic just need a different post callback
- `config/personality.md` — no changes
- `config/heartbeat.md` — no changes
- `config/news_summary.yaml` — no changes
- `docker-compose.yml` — SearXNG is unrelated to Slack/Discord
- `searxng/settings.yml` — same

### Needs changes
- `config.py` — swap Slack env vars for Discord
- `bot.py` — full rewrite (Slack Bolt → discord.py)
- `vision.py` — Discord attachments work differently than Slack files
- `news.py` — Slack posting callbacks → Discord posting callbacks
- `heartbeat.py` — Slack client → Discord client for posting
- `requirements.txt` — swap slack-bolt/slack-sdk for discord.py
- `.env.example` — new env vars
- `start.ps1` — remove Slack token checks
- `README.md` — Discord setup instructions

---

## Slack → Discord Concept Map

| Slack | Discord | Notes |
|-------|---------|-------|
| Workspace | Server (Guild) | |
| Bot User OAuth Token (`xoxb-`) | Bot Token | Single token, no app-level token needed |
| App-Level Token (`xapp-`) | *(not needed)* | Discord uses a single bot token |
| Socket Mode (WebSocket) | Gateway (WebSocket) | Same idea — no public URL needed |
| `slack_bolt` | `discord.py` | Primary bot library |
| `message.channels` event | `on_message` event | |
| `channels:history` scope | `MESSAGE_CONTENT` intent | Privileged intent — must enable in dev portal |
| `chat:write` scope | `Send Messages` permission | |
| `files:read` scope | *(not needed)* | Attachments come with the message object |
| `users:read` scope | `MEMBERS` intent | For resolving usernames |
| Channel threads | Discord threads | Similar concept, both supported |
| `/invite @bot` | Bot joins via OAuth URL | |
| `say()` | `channel.send()` | |
| Message `ts` (timestamp ID) | Message `id` | |
| `thread_ts` | `message.thread` / `message.reference` | |

---

## Discord App Setup (Manual, Before Coding)

1. Go to https://discord.com/developers/applications → **New Application**
2. Name it "Kibitz"
3. Upload the icon from `docs/kibitz-icon.png`
4. **Bot** tab:
   - Click **Add Bot**
   - Copy the **Bot Token** → `DISCORD_BOT_TOKEN`
   - Enable **Message Content Intent** (privileged — required to read message text)
   - Enable **Server Members Intent** (for username resolution)
5. **OAuth2 → URL Generator:**
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Read Message History`, `View Channels`, `Attach Files`
   - Copy the generated URL and open it to invite the bot to your server
6. Bot should appear in your server's member list

---

## Phase D0: Config & Dependencies

### Task D0.1 — Update requirements.txt
- **Do:** Replace `slack-bolt` and `slack-sdk` with `discord.py>=2.3.0`. Keep everything else.
- **Accept when:** `pip install -r requirements.txt` succeeds and `import discord` works.
- **Files:** `requirements.txt`

### Task D0.2 — Update config.py
- **Do:** Replace Slack env vars with Discord equivalents:
  - Remove: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`
  - Add: `DISCORD_BOT_TOKEN` (required)
  - Keep: `LLAMA_CPP_URL`, `SEARXNG_URL`, `BOT_NAME`, `CHANNEL_NAME`
  - Add: `GUILD_NAME` (optional, for multi-server safety — bot only responds in this server)
- **Accept when:** Config loads cleanly. Missing `DISCORD_BOT_TOKEN` exits with a clear error.
- **Files:** `config.py`, `.env.example`

---

## Phase D1: Vision Changes

### Task D1.1 — Update vision.py for Discord attachments
- **Do:** Discord message attachments have a public `.url` property (no auth header needed, unlike Slack's private URLs). Simplify `download_slack_image` → `download_image` that just fetches a URL without auth. Keep the base64 encoding helper.
- **Accept when:** Can download a Discord attachment URL and get base64 bytes.
- **Files:** `vision.py`

---

## Phase D2: Bot Core Rewrite

### Task D2.1 — Rewrite bot.py for discord.py
- **Do:** Rewrite `bot.py` using `discord.Client` (or `commands.Bot`). Key mappings:
  - `AsyncApp` → `discord.Client` with required intents (`message_content`, `guilds`, `members`)
  - `@app.event("message")` → `@client.event async def on_message(message)`
  - Ignore the bot's own messages via `message.author == client.user`
  - Ignore messages outside the target channel (`CHANNEL_NAME`)
  - `say(text)` → `message.channel.send(text)`
  - Resolve username from `message.author.display_name` (no API call needed)
  - Detect images from `message.attachments` where content_type starts with `image/`
  - Image URL is `attachment.url` (public, no auth needed)
  - Buffer, triage, responder, search, and vision wiring stays the same logic
- **Accept when:** Bot connects to Discord, receives messages in the target channel, buffers them, responds when triage says yes. Direct mentions always get a response. Mundane chatter is ignored.
- **Files:** `bot.py`

### Task D2.2 — Startup health checks
- **Do:** Keep the same startup checks (llama.cpp, SearXNG, config files) with retry logic. Remove Slack-specific checks (channel membership is implicit — if the bot is in the server and can see the channel, it works). Add a check that the target channel exists in the guild.
- **Accept when:** Bot refuses to start if llama.cpp or SearXNG is down. Logs a warning if the target channel isn't found.
- **Files:** `bot.py`

---

## Phase D3: News Digest for Discord

### Task D3.1 — Update news.py posting
- **Do:** The `run_news_digest` function already accepts `post_message` and `post_thread_reply` callables — this is transport-agnostic by design. No changes needed to `news.py` itself. The Discord-specific callables will be wired in `heartbeat.py`.
- **Accept when:** Verify `news.py` has no Slack imports. (It shouldn't — check only.)
- **Files:** `news.py` (verify, likely no changes)

---

## Phase D4: Heartbeat for Discord

### Task D4.1 — Update heartbeat.py for Discord client
- **Do:** Replace Slack client usage with Discord client:
  - `self.slack_client.chat_postMessage(channel=id, text=text)` → `channel.send(text)`
  - `self.slack_client.conversations_list()` → `guild.text_channels` lookup
  - Thread replies: use `message.create_thread()` or reply to the parent message
  - `_resolve_channel_id` → find channel by name from the Discord guild's channel list
- **Accept when:** Heartbeat tasks post to the correct Discord channel. News digest creates a thread for article replies.
- **Files:** `heartbeat.py`

---

## Phase D5: Cleanup & Docs

### Task D5.1 — Update start.ps1
- **Do:** Update the env var check to look for `DISCORD_BOT_TOKEN` instead of Slack tokens. Remove Slack-specific placeholder warnings.
- **Files:** `start.ps1`

### Task D5.2 — Update README.md
- **Do:** Replace Slack setup instructions with Discord setup (dev portal, intents, OAuth URL, bot invite). Update architecture section.
- **Files:** `README.md`

### Task D5.3 — Update TDD
- **Do:** Create a note or addendum in the TDD reflecting the Discord port. Or just update it in place.
- **Files:** `docs/slackbot-tdd.md`

---

## Phase Summary

| Phase | What | Key Files | Effort |
|-------|------|-----------|--------|
| D0 | Config & deps | config.py, requirements.txt, .env.example | Small |
| D1 | Vision changes | vision.py | Small |
| D2 | Bot core rewrite | bot.py | Large (main work) |
| D3 | News digest verify | news.py | Verify only |
| D4 | Heartbeat for Discord | heartbeat.py | Medium |
| D5 | Cleanup & docs | start.ps1, README.md | Small |

---

## Key Differences to Watch For

1. **Message Content Intent is privileged.** Must be enabled in the Discord dev portal AND in code (`intents.message_content = True`). Without it, `message.content` is empty for non-mention messages.

2. **No threading model like Slack.** Discord has threads but they're opt-in, not a reply-to-message system. For news digests, create a Discord thread off the parent message. For normal responses, just post in the channel.

3. **Mentions work differently.** Slack uses `@botname` in text. Discord uses `<@BOT_ID>` in the raw content, but `client.user.mentioned_in(message)` is the clean check. Update triage to check for both the bot name AND Discord mentions.

4. **No cooldown timestamp format change.** Slack uses string timestamps like `"1711756800.000000"`. Discord uses snowflake IDs. The buffer already stores timestamps as strings, so this is fine — but the heartbeat quiet-channel check parses them as floats. Use `message.created_at.timestamp()` when buffering instead.

5. **Images are simpler.** Discord attachments have public URLs. No auth header needed. This is strictly easier than Slack.
