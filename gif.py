import logging
import re

import aiohttp

from config import KLIPY_APP_KEY

logger = logging.getLogger(__name__)

# Pattern the LLM uses to request a GIF: [GIF: search terms]
GIF_PATTERN = re.compile(r"\[GIF:\s*(.+?)\]", re.IGNORECASE)

KLIPY_BASE = "https://api.klipy.com/api/v1"


async def search_gif(query: str) -> str | None:
    """Search Klipy for a GIF and return the URL, or None."""
    if not KLIPY_APP_KEY:
        logger.warning("No KLIPY_APP_KEY configured, skipping GIF search")
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KLIPY_BASE}/{KLIPY_APP_KEY}/gifs/search",
                params={
                    "q": query,
                    "customer_id": "kibitz-bot",
                    "per_page": 8,
                    "content_filter": "medium",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Klipy API returned status %d", resp.status)
                    return None
                data = await resp.json()
                results = data.get("data", {}).get("data", [])
                if not results:
                    return None
                # Get the GIF URL from the first result
                return results[0].get("file", {}).get("hd", {}).get("gif", {}).get("url")
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning("Klipy search failed: %s", e)
        return None


async def replace_gif_tags(text: str) -> str:
    """Find all [GIF: ...] tags in text and replace with actual Klipy GIF URLs."""
    matches = list(GIF_PATTERN.finditer(text))
    if not matches:
        return text

    for match in reversed(matches):  # reverse to preserve offsets
        query = match.group(1).strip()
        gif_url = await search_gif(query)
        if gif_url:
            text = text[:match.start()] + gif_url + text[match.end():]
        else:
            # Remove the tag if no GIF found
            text = text[:match.start()] + "(couldn't find a gif for that)" + text[match.end():]

    return text
