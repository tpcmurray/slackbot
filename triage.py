import json
import logging
from dataclasses import dataclass

from buffer import BufferedMessage
from config import BOT_NAME
from llm import chat_completion

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 300

TRIAGE_SYSTEM_PROMPT = """You are the triage module for a Slack bot named {bot_name}.
Given the recent conversation, decide if the bot should respond.

Rules:
- ALWAYS respond if someone mentions "{bot_name}" by name
- ALWAYS respond if someone asks a direct question and it seems directed at the bot
- MAYBE respond if there's an opportunity for humor, trash talk, or a useful contribution
- NEVER respond to mundane chatter, acknowledgments, or messages clearly between other people
- Respect the cooldown: the bot last spoke {seconds_ago} seconds ago. If < {cooldown_seconds}, only respond if directly addressed by name.

Reply with JSON only:
{{"should_respond": true/false, "reason": "brief explanation", "needs_search": false, "is_image_question": false}}"""


@dataclass
class TriageResult:
    should_respond: bool = False
    reason: str = ""
    needs_search: bool = False
    is_image_question: bool = False


def _format_conversation(messages: list[BufferedMessage]) -> str:
    lines = []
    for msg in messages:
        prefix = f"[{msg.username}]"
        if msg.has_image:
            prefix += " (attached an image)"
        lines.append(f"{prefix}: {msg.text}")
    return "\n".join(lines)


async def run_triage(
    recent_messages: list[BufferedMessage],
    seconds_since_last_response: float,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> TriageResult:
    """Tier 1: Quick triage to decide if the bot should respond."""
    system_prompt = TRIAGE_SYSTEM_PROMPT.format(
        bot_name=BOT_NAME,
        seconds_ago=int(seconds_since_last_response),
        cooldown_seconds=cooldown_seconds,
    )

    conversation = _format_conversation(recent_messages)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": conversation},
    ]

    response = await chat_completion(messages, temperature=0.1, max_tokens=128)

    if not response:
        return TriageResult()

    try:
        data = json.loads(response)
        return TriageResult(
            should_respond=bool(data.get("should_respond", False)),
            reason=str(data.get("reason", "")),
            needs_search=bool(data.get("needs_search", False)),
            is_image_question=bool(data.get("is_image_question", False)),
        )
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning("Triage returned malformed JSON: %s — %s", response, e)
        return TriageResult()
