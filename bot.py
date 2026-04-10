import asyncio
import logging
import sys
import time

import discord

from config import (
    DISCORD_BOT_TOKEN,
    BOT_NAME,
    CHANNEL_NAMES,
    PERSONALITY_PATH,
    HEARTBEAT_PATH,
    NEWS_SUMMARY_PATH,
)
from buffer import BufferedMessage, MessageBuffer
from triage import run_triage
from responder import generate_response
from search import two_phase_response, extract_urls, fetch_url_text
from vision import image_to_base64
from gif import replace_gif_tags
from llm import chat_completion, chat_completion_vision, health_check as llm_health_check
from search import health_check as search_health_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Discord intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

# Per-channel message buffers
buffers: dict[str, MessageBuffer] = {}
# Track when the bot last spoke per channel
last_response_time: dict[str, float] = {}


def get_buffer(channel_name: str) -> MessageBuffer:
    if channel_name not in buffers:
        buffers[channel_name] = MessageBuffer()
    return buffers[channel_name]


def _load_personality() -> str:
    """Load personality.md for two-phase responses."""
    try:
        text = PERSONALITY_PATH.read_text(encoding="utf-8")
        return text.replace("{BOT_NAME}", BOT_NAME)
    except FileNotFoundError:
        return f"You are {BOT_NAME}, a witty Discord bot."


def _format_conversation(messages: list[BufferedMessage]) -> str:
    lines = []
    for msg in messages:
        prefix = f"[{msg.username}]"
        if msg.has_image:
            prefix += " (attached an image)"
        lines.append(f"{prefix}: {msg.text}")
    return "\n".join(lines)


def _buffer_bot_response(channel_name: str, text: str):
    """Add the bot's own response to the buffer so it has context of what it said."""
    buf = get_buffer(channel_name)
    buf.add(BufferedMessage(
        timestamp=str(time.time()),
        user_id=str(client.user.id) if client.user else "bot",
        username=BOT_NAME,
        text=text,
    ))


def _find_channel_by_name(name: str) -> discord.TextChannel | None:
    """Find a channel by name across all guilds."""
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name == name:
                return channel
    return None


async def _send_response(channel: discord.TextChannel, text: str):
    """Process GIF tags and send a message, splitting at 2000 chars if needed."""
    text = await replace_gif_tags(text)
    for i in range(0, len(text), 2000):
        await channel.send(text[i:i+2000])


@client.event
async def on_ready():
    logger.info("Logged in as %s (ID: %s)", client.user.name, client.user.id)

    # Check target channel exists
    found_channels = []
    for guild in client.guilds:
        for channel in guild.text_channels:
            if channel.name in CHANNEL_NAMES:
                found_channels.append(channel)

    if not found_channels:
        logger.error("STARTUP FAILED: None of channels %s found in any server", CHANNEL_NAMES)
        await client.close()
        return

    for ch in found_channels:
        logger.info("Listening in #%s (guild: %s)", ch.name, ch.guild.name)

    # Backfill message buffers with recent channel history
    for ch in found_channels:
        try:
            history = []
            async for msg in ch.history(limit=50):
                history.append(msg)
            history.reverse()  # oldest first
            buf = get_buffer(ch.name)
            for msg in history:
                has_image = any(a.content_type and a.content_type.startswith("image/") for a in msg.attachments)
                buf.add(BufferedMessage(
                    timestamp=str(msg.created_at.timestamp()),
                    user_id=str(msg.author.id),
                    username=msg.author.display_name,
                    text=msg.content or "",
                    has_image=has_image,
                    image_urls=[a.url for a in msg.attachments if a.content_type and a.content_type.startswith("image/")],
                    thread_ts=str(msg.reference.message_id) if msg.reference else None,
                ))
            logger.info("Backfilled %d messages from #%s", len(history), ch.name)
        except Exception as e:
            logger.warning("Failed to backfill history for #%s: %s", ch.name, e)

    # Start heartbeat scheduler
    from heartbeat import HeartbeatScheduler
    scheduler = HeartbeatScheduler(client, buffers)
    client.loop.create_task(scheduler.run_loop())
    logger.info("Heartbeat scheduler started")
    logger.info("Kibitz is online.")


@client.event
async def on_message(message: discord.Message):
    # Ignore the bot's own messages
    if message.author == client.user:
        return

    # Ignore DMs
    if not message.guild:
        return

    # Only respond in the target channel
    if message.channel.name not in CHANNEL_NAMES:
        return

    channel_name = message.channel.name
    text = message.content
    ts = str(message.created_at.timestamp())
    username = message.author.display_name

    # Check for images (attachments + embeds)
    image_attachments = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
    image_urls = [a.url for a in image_attachments]
    # Discord also puts pasted/linked images in embeds
    for embed in message.embeds:
        if embed.image and embed.image.url:
            image_urls.append(embed.image.url)
        if embed.thumbnail and embed.thumbnail.url:
            image_urls.append(embed.thumbnail.url)
    has_image = len(image_urls) > 0

    # Check for name mention (Discord @mention or text mention)
    mentioned = client.user.mentioned_in(message) or BOT_NAME.lower() in text.lower()

    # Buffer the message
    buf = get_buffer(channel_name)
    buf.add(BufferedMessage(
        timestamp=ts,
        user_id=str(message.author.id),
        username=username,
        text=text,
        has_image=has_image,
        image_urls=image_urls,
        thread_ts=str(message.reference.message_id) if message.reference else None,
    ))

    # Run triage (pure string matching — no LLM call)
    triage_result = run_triage(buf.recent(10), mentioned, has_image)

    if not triage_result.should_respond:
        return

    logger.info("Triage: responding — %s", triage_result.reason)

    # Resolve target channel (cross-posting support)
    target = message.channel
    target_name = channel_name
    if triage_result.target_channel:
        cross = _find_channel_by_name(triage_result.target_channel)
        if cross:
            target = cross
            target_name = cross.name
            logger.info("Cross-posting to #%s", target_name)
        else:
            logger.warning("Target channel #%s not found, posting in #%s", triage_result.target_channel, channel_name)

    # Handle image questions
    if triage_result.is_image_question and image_urls:
        image_b64 = await image_to_base64(image_urls[0])
        if image_b64:
            personality = _load_personality()
            # First, get a detailed description to store for follow-up context
            detailed = await chat_completion_vision(
                text=(
                    "Describe this image in thorough detail. Include all visible text, "
                    "labels, names, locations, and any other specifics. "
                    "This description will be used to answer follow-up questions, "
                    "so be exhaustive and accurate. Do not make anything up."
                ),
                image_base64=image_b64,
                system_prompt="You are a precise image describer. List everything you see.",
                max_tokens=4096,
            )
            # Store the detailed description in the buffer for follow-ups
            if detailed:
                buf.add(BufferedMessage(
                    timestamp=str(time.time()),
                    user_id=str(client.user.id) if client.user else "bot",
                    username=f"{BOT_NAME} (image analysis)",
                    text=f"[Detailed image description: {detailed}]",
                ))
                logger.info("Stored image description (%d chars) in buffer", len(detailed))

            # Now generate the user-facing response with personality
            response = await chat_completion_vision(
                text=text or "What's in this image?",
                image_base64=image_b64,
                system_prompt=personality,
            )
            if response:
                await _send_response(target, response)
                _buffer_bot_response(target_name, response)
                last_response_time[channel_name] = time.time()
            return

    # If the message contains URLs, fetch their content and include in the prompt
    urls = extract_urls(text)
    if urls:
        url_contents = []
        for url in urls[:2]:  # max 2 URLs
            content = await fetch_url_text(url)
            if content:
                url_contents.append(f"[Content from {url}]:\n{content}")
        if url_contents:
            personality = _load_personality()
            url_context = "\n\n".join(url_contents)
            messages = [
                {"role": "system", "content": personality},
                {
                    "role": "user",
                    "content": (
                        f"Someone said: {text}\n\n"
                        f"Here is the content from the URL(s) they shared:\n{url_context}\n\n"
                        "Summarize the article in a few paragraphs. Cover the key points, "
                        "main arguments, and any notable details. Keep your personality "
                        "but be thorough — this is a summary, not a one-liner."
                    ),
                },
            ]
            response = await chat_completion(messages)
            if response:
                try:
                    await _send_response(target, response)
                    _buffer_bot_response(target_name, response)
                    last_response_time[channel_name] = time.time()
                except discord.HTTPException as e:
                    logger.error("Failed to send URL response: %s", e)
            return

    # Handle factual/search questions
    if triage_result.needs_search:
        personality = _load_personality()
        conversation = _format_conversation(buf.recent(10))

        async def post(msg: str):
            try:
                await _send_response(target, msg)
                _buffer_bot_response(target_name, msg)
                last_response_time[channel_name] = time.time()
            except discord.HTTPException as e:
                logger.error("Failed to send search response: %s", e)

        await two_phase_response(text, conversation, personality, post)
        return

    # Standard personality-driven response
    response = await generate_response(buf.recent(15))
    if response:
        logger.info("Sending response (%d chars): %s", len(response), response[:100])
        try:
            await _send_response(target, response)
            _buffer_bot_response(target_name, response)
            last_response_time[channel_name] = time.time()
        except discord.Forbidden:
            logger.error("Bot lacks permission to send messages in #%s", target_name)
        except discord.HTTPException as e:
            logger.error("Failed to send message: %s", e)
    else:
        logger.warning("Responder returned empty response")


async def _wait_for_service(name: str, check_fn, retries: int = 10, delay: float = 3.0) -> bool:
    """Retry a health check function until it passes or retries are exhausted."""
    for attempt in range(1, retries + 1):
        if await check_fn():
            return True
        logger.info("Waiting for %s... (attempt %d/%d)", name, attempt, retries)
        await asyncio.sleep(delay)
    return False


async def startup_checks() -> bool:
    """Verify all dependencies are reachable, with retries for services that are still starting."""
    passed = True

    # Check llama.cpp (up to 30s)
    if not await _wait_for_service("llama.cpp", llm_health_check):
        logger.error("STARTUP FAILED: llama.cpp not reachable at configured URL")
        passed = False

    # Check SearXNG (up to 30s)
    if not await _wait_for_service("SearXNG", search_health_check):
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
    # Run startup checks before connecting to Discord
    if not await startup_checks():
        sys.exit(1)

    await client.start(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
