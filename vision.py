import base64
import logging

import aiohttp

logger = logging.getLogger(__name__)


async def download_image(url: str) -> bytes:
    """Download an image from a URL."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error("Failed to download image (status %d): %s", resp.status, url)
                return b""
            return await resp.read()


async def image_to_base64(url: str) -> str:
    """Download an image and return it as a base64-encoded string."""
    image_bytes = await download_image(url)
    if not image_bytes:
        return ""
    return base64.b64encode(image_bytes).decode("utf-8")
