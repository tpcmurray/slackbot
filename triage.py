import json
import logging
import re
from dataclasses import dataclass

from buffer import BufferedMessage
from config import BOT_NAME
from llm import chat_completion

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 300

TRIAGE_SYSTEM_PROMPT = """You are the triage module for a Discord bot named {bot_name}.
Given the recent conversation, decide if the bot should respond.

Rules:
- ONLY respond if someone mentions "{bot_name}" by name (or a close variation like "@{bot_name}")
- NEVER respond unless "{bot_name}" appears in the message
- No exceptions. No cooldown logic needed. Name mention is the only trigger.

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

    response = await chat_completion(messages, temperature=0.1, max_tokens=512)

    if not response:
        logger.warning("Triage got empty LLM response")
        return TriageResult()

    logger.debug("Triage raw response: %s", response)

    # Extract JSON from the response — handle code fences, truncation, etc.
    cleaned = response.strip()

    # Strip markdown code fences
    if "```" in cleaned:
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(1).strip()

    # Find the JSON object starting with {
    json_start = cleaned.find("{")
    if json_start != -1:
        cleaned = cleaned[json_start:]

    # If JSON is truncated (no closing brace), try to close it
    if cleaned.startswith("{") and "}" not in cleaned:
        cleaned = cleaned + "}"

    try:
        data = json.loads(cleaned)
        result = TriageResult(
            should_respond=bool(data.get("should_respond", False)),
            reason=str(data.get("reason", "")),
            needs_search=bool(data.get("needs_search", False)),
            is_image_question=bool(data.get("is_image_question", False)),
        )
        logger.info("Triage result: should_respond=%s, reason=%s", result.should_respond, result.reason)
        return result
    except (json.JSONDecodeError, AttributeError) as e:
        # Last resort: regex extract individual fields from malformed JSON
        logger.warning("Triage JSON parse failed, trying field extraction: %s", e)
        should = re.search(r'"should_respond"\s*:\s*(true|false)', response, re.IGNORECASE)
        reason = re.search(r'"reason"\s*:\s*"([^"]*)"', response)
        search = re.search(r'"needs_search"\s*:\s*(true|false)', response, re.IGNORECASE)
        image = re.search(r'"is_image_question"\s*:\s*(true|false)', response, re.IGNORECASE)

        if should:
            result = TriageResult(
                should_respond=should.group(1).lower() == "true",
                reason=reason.group(1) if reason else "",
                needs_search=search.group(1).lower() == "true" if search else False,
                is_image_question=image.group(1).lower() == "true" if image else False,
            )
            logger.info("Triage result (extracted): should_respond=%s, reason=%s", result.should_respond, result.reason)
            return result

        logger.warning("Triage could not extract fields from: %s", response[:200])
        return TriageResult()
