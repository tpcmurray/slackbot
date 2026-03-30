import logging
from typing import Optional

import aiohttp

from config import LLAMA_CPP_URL

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
    max_tokens: int = 512,
) -> str:
    """Send a chat completion request to llama.cpp and return the response text."""
    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

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
                return data["choices"][0]["message"]["content"]

    except (aiohttp.ClientError, TimeoutError) as e:
        logger.error("LLM request failed: %s", e)
        return ""


async def chat_completion_vision(
    text: str,
    image_base64: str,
    system_prompt: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 512,
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
