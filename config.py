import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_REQUIRED_ENV_VARS = [
    "DISCORD_BOT_TOKEN",
]

_missing = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}", file=sys.stderr)
    sys.exit(1)

DISCORD_BOT_TOKEN: str = os.environ["DISCORD_BOT_TOKEN"]
LLAMA_CPP_URL: str = os.getenv("LLAMA_CPP_URL", "http://localhost:8080")
SEARXNG_URL: str = os.getenv("SEARXNG_URL", "http://localhost:8888")
BOT_NAME: str = os.getenv("BOT_NAME", "kibitz")
CHANNEL_NAMES: list[str] = [
    c.strip() for c in os.getenv("CHANNEL_NAMES", "general").split(",")
]

# Config file paths
CONFIG_DIR = Path(__file__).parent / "config"
PERSONALITY_PATH = CONFIG_DIR / "personality.md"
HEARTBEAT_PATH = CONFIG_DIR / "heartbeat.md"
NEWS_SUMMARY_PATH = CONFIG_DIR / "news_summary.yaml"
