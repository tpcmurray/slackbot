import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp

from config import LLAMA_CPP_URL

TZ = ZoneInfo("America/St_Johns")

logger = logging.getLogger(__name__)


async def health_check() -> bool:
    """Check if llama.cpp server is reachable."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{LLAMA_CPP_URL}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status == 200
    except (aiohttp.ClientError, TimeoutError):
        return False


async def chat_completion(
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    _retries: int = 3,
) -> str:
    """Send a chat completion request to llama.cpp and return the response text.

    Retries up to _retries times if the model returns empty content.
    """
    # Inject current date/time into the first system message
    now = datetime.now(TZ).strftime("%A, %B %d, %Y at %I:%M %p NST")
    messages = [m.copy() for m in messages]
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = f"Current date and time: {now}\n\n{messages[0]['content']}"
    else:
        messages.insert(0, {"role": "system", "content": f"Current date and time: {now}"})

    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    for attempt in range(1, _retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{LLAMA_CPP_URL}/v1/chat/completions",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error("LLM returned status %d: %s", resp.status, body)
                        return ""

                    data = await resp.json()
                    choice = data["choices"][0]["message"]
                    finish_reason = data["choices"][0].get("finish_reason", "")
                    content = choice.get("content", "") or ""

                    # With enable_thinking: false, reasoning models put everything
                    # in content with </think> separating reasoning from the answer
                    if "</think>" in content:
                        content = content.split("</think>")[-1].strip()

                    # Fallback: if content is empty, check reasoning_content
                    if not content:
                        reasoning = choice.get("reasoning_content", "") or ""
                        logger.debug("Empty content, reasoning_content (%d chars): %s", len(reasoning), reasoning[:300] if reasoning else "(empty)")
                        if "</think>" in reasoning:
                            content = reasoning.split("</think>")[-1].strip()
                        elif reasoning and not _is_reasoning_text(reasoning):
                            # reasoning_content IS the answer (some model configs)
                            content = reasoning.strip()

                    # If the model hit max_tokens mid-reasoning, discard it
                    if content and _is_reasoning_text(content):
                        logger.warning("LLM hit token limit mid-reasoning (attempt %d/%d), discarding %d chars", attempt, _retries, len(content))
                        content = ""

                    if content:
                        logger.info("LLM response (%d chars): %s", len(content), content[:200])
                        return content

                    # Bump max_tokens on retry in case the model needs more room
                    payload["max_tokens"] = min(payload["max_tokens"] * 2, 8192)
                    logger.warning("LLM returned empty content (attempt %d/%d), retrying with max_tokens=%d", attempt, _retries, payload["max_tokens"])

        except (aiohttp.ClientError, TimeoutError) as e:
            logger.error("LLM request failed (attempt %d/%d): %s", attempt, _retries, e)

    logger.error("LLM failed after %d attempts", _retries)
    return ""


def _is_reasoning_text(text: str) -> bool:
    """Detect if text is chain-of-thought reasoning rather than a real answer."""
    indicators = [
        "Thinking Process",
        "Analyze the Request",
        "Determine the Response",
        "Drafting Options",
        "Let's go with",
        "I'll go with",
        "**Analyze",
        "**Draft",
        "Step 1:",
        "Step 2:",
    ]
    # If the text starts with or heavily contains reasoning markers, it's CoT
    first_200 = text[:200]
    return any(marker in first_200 for marker in indicators)


async def chat_completion_vision(
    text: str,
    image_base64: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """Send a vision chat completion with a base64-encoded image."""
    messages: list[dict] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.append({
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
            {"type": "text", "text": text},
        ],
    })

    return await chat_completion(messages, temperature=temperature, max_tokens=max_tokens)
