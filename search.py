import logging
from typing import Any, Callable, Coroutine

import aiohttp

from config import SEARXNG_URL
from llm import chat_completion

logger = logging.getLogger(__name__)


async def health_check() -> bool:
    """Check if SearXNG is reachable."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SEARXNG_URL}/search",
                params={"q": "test", "format": "json"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
    except (aiohttp.ClientError, TimeoutError):
        return False


async def searxng_query(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Query SearXNG and return a list of {title, url, content} dicts."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    logger.error("SearXNG returned status %d", resp.status)
                    return []

                data = await resp.json()
                results = []
                for r in data.get("results", [])[:max_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "content": r.get("content", ""),
                    })
                return results

    except (aiohttp.ClientError, TimeoutError) as e:
        logger.error("SearXNG query failed: %s", e)
        return []


async def two_phase_response(
    question: str,
    conversation_context: str,
    personality: str,
    post_message: Callable[[str], Coroutine[Any, Any, None]],
) -> None:
    """Two-phase factual response: quick gut reaction, then researched answer.

    Args:
        question: The user's question text.
        conversation_context: Formatted recent conversation for context.
        personality: The personality system prompt.
        post_message: Async callable to post a message to Slack.
    """
    # Phase 1: Quick gut-reaction answer
    quick_messages = [
        {"role": "system", "content": personality},
        {
            "role": "user",
            "content": (
                f"Someone in the Slack channel asked: {question}\n\n"
                f"Recent conversation:\n{conversation_context}\n\n"
                "Give a quick, short gut-reaction answer from your own knowledge. "
                "End with something like 'let me look that up' or 'hold on, checking...' "
                "to signal you'll follow up with a better answer."
            ),
        },
    ]

    quick_answer = await chat_completion(quick_messages, temperature=0.7, max_tokens=256)
    if quick_answer:
        await post_message(quick_answer)

    # Phase 2: Search + researched response
    search_results = await searxng_query(question)

    if not search_results:
        follow_up = await chat_completion(
            [
                {"role": "system", "content": personality},
                {"role": "user", "content": f"I tried to search for '{question}' but got no results. Let the channel know briefly."},
            ],
            temperature=0.7,
            max_tokens=128,
        )
        if follow_up:
            await post_message(follow_up)
        return

    sources = "\n".join(
        f"- {r['title']}: {r['content']} ({r['url']})"
        for r in search_results
    )

    researched_messages = [
        {"role": "system", "content": personality},
        {
            "role": "user",
            "content": (
                f"Earlier someone asked: {question}\n\n"
                f"Here are search results:\n{sources}\n\n"
                "Now give a researched answer based on these results. "
                "Be concise but accurate. Include a source link if relevant."
            ),
        },
    ]

    researched_answer = await chat_completion(researched_messages, temperature=0.5, max_tokens=512)
    if researched_answer:
        await post_message(researched_answer)
