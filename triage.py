import logging
import re
from dataclasses import dataclass

from buffer import BufferedMessage
from config import BOT_NAME, CHANNEL_NAMES

logger = logging.getLogger(__name__)

# Patterns for detecting intent from the message text
_SEARCH_PATTERNS = re.compile(
    r"\b(search|look up|google|find|what is|what are|who is|who are|when did|where is|how do|how does|tell me about|get me a link)\b",
    re.IGNORECASE,
)
_IMAGE_PATTERNS = re.compile(
    r"\b(what('s| is) (this|that|in this) (image|picture|photo|pic)|describe (this|the) (image|picture|photo)|what do you see)\b",
    re.IGNORECASE,
)
_CHANNEL_PATTERN = re.compile(
    r"\b(?:post|say|send|put|drop)\s+(?:.*?\s+)?(?:in|to)\s+#?(\w+)\b",
    re.IGNORECASE,
)


@dataclass
class TriageResult:
    should_respond: bool = False
    reason: str = ""
    needs_search: bool = False
    is_image_question: bool = False
    target_channel: str = ""


def run_triage(
    recent_messages: list[BufferedMessage],
    mentioned: bool,
    has_image: bool,
) -> TriageResult:
    """Tier 1: Fast string-match triage — no LLM call needed."""
    if not mentioned:
        return TriageResult(reason="not mentioned")

    if not recent_messages:
        return TriageResult(should_respond=True, reason="mentioned (no context)")

    last_msg = recent_messages[-1]
    text = last_msg.text

    # Detect cross-post target
    target_channel = ""
    channel_match = _CHANNEL_PATTERN.search(text)
    if channel_match:
        candidate = channel_match.group(1).lower()
        if candidate in CHANNEL_NAMES:
            target_channel = candidate

    # Detect search intent
    needs_search = bool(_SEARCH_PATTERNS.search(text))

    # Detect image question
    is_image_question = has_image and bool(_IMAGE_PATTERNS.search(text))

    reason = "name mentioned"
    if needs_search:
        reason += " + search intent"
    if is_image_question:
        reason += " + image question"
    if target_channel:
        reason += f" + cross-post to #{target_channel}"

    return TriageResult(
        should_respond=True,
        reason=reason,
        needs_search=needs_search,
        is_image_question=is_image_question,
        target_channel=target_channel,
    )
