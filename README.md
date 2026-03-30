<p align="center">
  <img src="docs/kibitz-icon.png" alt="Kibitz" width="200">
</p>

<h1 align="center">Kibitz</h1>

<p align="center">
  A Discord bot that passively reads the channel and autonomously decides when to chime in.<br>
  Yiddish for an onlooker who offers unsolicited advice — literally what this bot does.
</p>

---

## Prerequisites

- **Python 3.11+**
- **Docker** (for SearXNG)
- **llama.cpp** with `--mmproj` support (for vision)
- A **Discord server** you admin

## Setup

### 1. Clone and install dependencies

```bash
cd slackbot
pip install -r requirements.txt
```

### 2. Create your `.env`

Copy the example and fill in your Discord bot token:

```bash
cp .env.example .env
```

```
DISCORD_BOT_TOKEN=your-bot-token-here
LLAMA_CPP_URL=http://localhost:8080
SEARXNG_URL=http://localhost:8888
BOT_NAME=kibitz
CHANNEL_NAME=general
```

### 3. Discord app setup

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create a **New Application**
2. Name it "Kibitz" and upload the icon from `docs/kibitz-icon.png`
3. Go to the **Bot** tab:
   - Copy the **Bot Token** — this is your `DISCORD_BOT_TOKEN`
   - Enable **Message Content Intent** (required to read message text)
   - Enable **Server Members Intent** (for username resolution)
4. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Read Message History`, `View Channels`, `Attach Files`, `Create Public Threads`
   - Open the generated URL to invite the bot to your server

### 4. Start llama.cpp

```bash
llama-server \
  -m Qwen3.5-9B.Q8_0.gguf \
  --mmproj mmproj-BF16.gguf \
  --port 8080 \
  --host 127.0.0.1 \
  -c 8192 \
  -ngl 99
```

### 5. Start SearXNG

```bash
docker compose up -d
```

Verify it's running:

```bash
curl http://localhost:8888/search?q=test&format=json
```

### 6. Start the bot

```bash
python bot.py
```

Or use the all-in-one startup script:

```powershell
.\start.ps1
```

On startup the bot verifies that llama.cpp, SearXNG, and Discord are all reachable. If anything is down it will retry a few times then exit with a clear error.

## Configuration

Kibitz uses editable config files — no code changes needed to tune behavior:

| File | Purpose |
|------|---------|
| `config/personality.md` | Bot personality and tone (loaded as the LLM system prompt) |
| `config/heartbeat.md` | Scheduled autonomous tasks (news digest, quiet channel prompts) |
| `config/news_summary.yaml` | RSS feeds, relevance criteria, and digest settings |

All config files are re-read on use, so edits take effect without restarting the bot.

## Architecture

- **Discord** connects via Gateway WebSocket (no public URL needed)
- **LLM** is a local Qwen 3.5 9B via llama.cpp's OpenAI-compatible API
- **Search** is self-hosted SearXNG in Docker
- **Two-tier response**: cheap triage on every message, full generation only when warranted
- **Two-phase factual answers**: quick gut reaction, then a researched follow-up via SearXNG
- **News digest**: daily RSS-based summary ranked by LLM, posted as threaded messages
