import asyncio
import logging
import sys
import time

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from config import (
    SLACK_BOT_TOKEN,
    SLACK_APP_TOKEN,
    BOT_NAME,
    CHANNEL_NAME,
    PERSONALITY_PATH,
    HEARTBEAT_PATH,
    NEWS_SUMMARY_PATH,
)
from buffer import BufferedMessage, MessageBuffer
from triage import run_triage
from responder import generate_response
from search import two_phase_response
from vision import slack_image_to_base64
from llm import chat_completion_vision, health_check as llm_health_check
from search import health_check as search_health_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = AsyncApp(token=SLACK_BOT_TOKEN)

# Per-channel message buffers
buffers: dict[str, MessageBuffer] = {}
# Track when the bot last spoke per channel
last_response_time: dict[str, float] = {}
# Cache user ID -> display name
user_cache: dict[str, str] = {}
# The bot's own user ID (resolved at startup)
bot_user_id: str | None = None


def get_buffer(channel: str) -> MessageBuffer:
    if channel not in buffers:
        buffers[channel] = MessageBuffer()
    return buffers[channel]


async def resolve_username(user_id: str) -> str:
    """Resolve a Slack user ID to a display name, with caching."""
    if user_id in user_cache:
        return user_cache[user_id]

    try:
        result = await app.client.users_info(user=user_id)
        name = (
            result["user"].get("profile", {}).get("display_name")
            or result["user"].get("real_name")
            or result["user"].get("name")
            or user_id
        )
        user_cache[user_id] = name
        return name
    except Exception as e:
        logger.warning("Failed to resolve user %s: %s", user_id, e)
        return user_id


def _load_personality() -> str:
    """Load personality.md for two-phase responses."""
    try:
        text = PERSONALITY_PATH.read_text(encoding="utf-8")
        return text.replace("{BOT_NAME}", BOT_NAME)
    except FileNotFoundError:
        return f"You are {BOT_NAME}, a witty Slack bot."


def _format_conversation(messages: list[BufferedMessage]) -> str:
    lines = []
    for msg in messages:
        prefix = f"[{msg.username}]"
        if msg.has_image:
            prefix += " (attached an image)"
        lines.append(f"{prefix}: {msg.text}")
    return "\n".join(lines)


@app.event("message")
async def handle_message(event, say):
    global bot_user_id

    # Ignore bot's own messages
    user_id = event.get("user", "")
    if user_id == bot_user_id:
        return

    # Ignore message subtypes (edits, deletes, joins, etc.) except file_share
    subtype = event.get("subtype")
    if subtype and subtype != "file_share":
        return

    channel = event.get("channel", "")
    text = event.get("text", "")
    ts = event.get("ts", "")
    thread_ts = event.get("thread_ts")

    # Resolve username
    username = await resolve_username(user_id)

    # Check for images
    files = event.get("files", [])
    image_files = [f for f in files if f.get("mimetype", "").startswith("image/")]
    has_image = len(image_files) > 0
    image_urls = [f.get("url_private_download", "") for f in image_files if f.get("url_private_download")]

    # Buffer the message
    buf = get_buffer(channel)
    buf.add(BufferedMessage(
        timestamp=ts,
        user_id=user_id,
        username=username,
        text=text,
        has_image=has_image,
        image_urls=image_urls,
        thread_ts=thread_ts,
    ))

    # Run triage
    seconds_ago = time.time() - last_response_time.get(channel, 0)
    triage_result = await run_triage(buf.recent(10), seconds_ago)

    if not triage_result.should_respond:
        return

    logger.info("Triage: responding — %s", triage_result.reason)

    # Handle image questions
    if triage_result.is_image_question and image_urls:
        image_b64 = await slack_image_to_base64(image_urls[0])
        if image_b64:
            personality = _load_personality()
            response = await chat_completion_vision(
                text=text or "What's in this image?",
                image_base64=image_b64,
                system_prompt=personality,
            )
            if response:
                await say(response)
                last_response_time[channel] = time.time()
            return

    # Handle factual/search questions
    if triage_result.needs_search:
        personality = _load_personality()
        conversation = _format_conversation(buf.recent(10))

        async def post(msg: str):
            await say(msg)
            last_response_time[channel] = time.time()

        await two_phase_response(text, conversation, personality, post)
        return

    # Standard personality-driven response
    response = await generate_response(buf.full_context())
    if response:
        await say(response)
        last_response_time[channel] = time.time()


async def startup_checks() -> bool:
    """Verify all dependencies are reachable. Returns True if all pass."""
    passed = True

    # Check llama.cpp
    if not await llm_health_check():
        logger.error("STARTUP FAILED: llama.cpp not reachable at configured URL")
        passed = False

    # Check SearXNG
    if not await search_health_check():
        logger.error("STARTUP FAILED: SearXNG not reachable at configured URL")
        passed = False

    # Check config files
    for path, name in [
        (PERSONALITY_PATH, "personality.md"),
        (HEARTBEAT_PATH, "heartbeat.md"),
        (NEWS_SUMMARY_PATH, "news_summary.yaml"),
    ]:
        if not path.exists():
            logger.error("STARTUP FAILED: Config file missing: %s", name)
            passed = False

    return passed


async def main():
    global bot_user_id

    # Run startup checks
    if not await startup_checks():
        sys.exit(1)

    # Resolve the bot's own user ID
    try:
        auth = await app.client.auth_test()
        bot_user_id = auth["user_id"]
        logger.info("Bot user ID: %s", bot_user_id)
    except Exception as e:
        logger.error("Failed to authenticate with Slack: %s", e)
        sys.exit(1)

    # Check the bot is in the target channel
    try:
        result = await app.client.conversations_list(types="public_channel", limit=200)
        channels = result.get("channels", [])
        target = next((c for c in channels if c["name"] == CHANNEL_NAME), None)

        if not target:
            logger.error("STARTUP FAILED: Channel #%s not found", CHANNEL_NAME)
            sys.exit(1)

        if not target.get("is_member", False):
            logger.error("STARTUP FAILED: Bot is not a member of #%s — use /invite @%s", CHANNEL_NAME, BOT_NAME)
            sys.exit(1)

        logger.info("Bot is in #%s", CHANNEL_NAME)
    except Exception as e:
        logger.error("Failed to check channel membership: %s", e)
        sys.exit(1)

    # Start heartbeat scheduler
    from heartbeat import HeartbeatScheduler
    scheduler = HeartbeatScheduler(app.client, buffers)
    asyncio.create_task(scheduler.run_loop())
    logger.info("Heartbeat scheduler started")

    # Start socket mode
    handler = AsyncSocketModeHandler(app, SLACK_APP_TOKEN)
    logger.info("Kibitz is online.")
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
