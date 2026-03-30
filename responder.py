import logging

from buffer import BufferedMessage
from config import BOT_NAME, PERSONALITY_PATH
from llm import chat_completion

logger = logging.getLogger(__name__)


def _load_personality() -> str:
    """Load personality.md and substitute the bot name. Re-read each call for live edits."""
    try:
        text = PERSONALITY_PATH.read_text(encoding="utf-8")
        return text.replace("{BOT_NAME}", BOT_NAME)
    except FileNotFoundError:
        logger.error("personality.md not found at %s", PERSONALITY_PATH)
        return f"You are {BOT_NAME}, a witty Discord bot."


def _format_conversation(messages: list[BufferedMessage]) -> str:
    lines = []
    for msg in messages:
        prefix = f"[{msg.username}]"
        if msg.has_image:
            prefix += " (attached an image)"
        lines.append(f"{prefix}: {msg.text}")
    return "\n".join(lines)


async def generate_response(
    full_context: list[BufferedMessage],
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """Tier 2: Generate a personality-driven response using the full message buffer."""
    personality = _load_personality()
    conversation = _format_conversation(full_context)

    messages = [
        {"role": "system", "content": personality},
        {
            "role": "user",
            "content": (
                f"Recent chat:\n{conversation}\n\n"
                "Reply as your next message. One or two sentences max. No name prefix."
            ),
        },
    ]

    return await chat_completion(messages, temperature=temperature, max_tokens=max_tokens)
