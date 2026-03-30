import base64
import logging

import aiohttp

from config import SLACK_BOT_TOKEN

logger = logging.getLogger(__name__)


async def download_slack_image(url: str) -> bytes:
    """Download an image from a Slack private URL using the bot token."""
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error("Failed to download image (status %d): %s", resp.status, url)
                return b""
            return await resp.read()


async def slack_image_to_base64(url: str) -> str:
    """Download a Slack image and return it as a base64-encoded string."""
    image_bytes = await download_slack_image(url)
    if not image_bytes:
        return ""
    return base64.b64encode(image_bytes).decode("utf-8")
