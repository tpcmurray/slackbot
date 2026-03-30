import asyncio
import logging
import re
from datetime import datetime
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from config import (
    BOT_NAME,
    CHANNEL_NAME,
    HEARTBEAT_PATH,
    PERSONALITY_PATH,
)
from buffer import MessageBuffer
from llm import chat_completion
from news import run_news_digest

logger = logging.getLogger(__name__)

TZ = ZoneInfo("America/St_Johns")


@dataclass
class HeartbeatTask:
    name: str
    task_type: str  # "news_digest" or "quiet_channel"
    schedule_hour: int | None = None
    schedule_minute: int | None = None
    interval_hours: int | None = None
    window_start: int | None = None
    window_end: int | None = None
    quiet_threshold_hours: int = 3


def parse_heartbeat_file(path: str) -> list[HeartbeatTask]:
    """Parse heartbeat.md into a list of tasks."""
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        logger.error("heartbeat.md not found at %s", path)
        return []

    tasks = []
    sections = re.split(r"^## ", text, flags=re.MULTILINE)

    for section in sections:
        if not section.strip():
            continue

        name = section.split("\n")[0].strip()
        body = section.lower()

        if "news digest" in name.lower() or "news digest" in body:
            # Parse "Daily at HH:MM NST"
            time_match = re.search(r"daily at (\d{1,2}):(\d{2})", body)
            if time_match:
                tasks.append(HeartbeatTask(
                    name=name,
                    task_type="news_digest",
                    schedule_hour=int(time_match.group(1)),
                    schedule_minute=int(time_match.group(2)),
                ))

        elif "quiet" in name.lower() or "quiet" in body:
            # Parse "Every N hours during HH:MM-HH:MM NST"
            interval_match = re.search(r"every (\d+) hours?", body)
            window_match = re.search(r"during (\d{1,2}):\d{2}-(\d{1,2}):\d{2}", body)
            threshold_match = re.search(r"no messages.*?(\d+) hours?", body)

            tasks.append(HeartbeatTask(
                name=name,
                task_type="quiet_channel",
                interval_hours=int(interval_match.group(1)) if interval_match else 4,
                window_start=int(window_match.group(1)) if window_match else 10,
                window_end=int(window_match.group(2)) if window_match else 22,
                quiet_threshold_hours=int(threshold_match.group(1)) if threshold_match else 3,
            ))

    return tasks


class HeartbeatScheduler:
    def __init__(self, slack_client, buffers: dict[str, MessageBuffer]):
        self.slack_client = slack_client
        self.buffers = buffers
        self.last_runs: dict[str, datetime] = {}

    async def run_loop(self):
        """Main heartbeat loop — runs every 60 seconds."""
        while True:
            try:
                tasks = parse_heartbeat_file(str(HEARTBEAT_PATH))
                now = datetime.now(TZ)

                for task in tasks:
                    if self._should_run(task, now):
                        await self._execute(task, now)
                        self.last_runs[task.name] = now

            except Exception as e:
                logger.error("Heartbeat loop error: %s", e)

            await asyncio.sleep(60)

    def _should_run(self, task: HeartbeatTask, now: datetime) -> bool:
        """Check if a task should run based on its schedule."""
        last_run = self.last_runs.get(task.name)

        if task.task_type == "news_digest":
            if task.schedule_hour is None or task.schedule_minute is None:
                return False
            # Run if we're at the scheduled minute and haven't run today
            if now.hour == task.schedule_hour and now.minute == task.schedule_minute:
                if not last_run or last_run.date() < now.date():
                    return True
            return False

        elif task.task_type == "quiet_channel":
            # Check time window
            if task.window_start is not None and task.window_end is not None:
                if not (task.window_start <= now.hour < task.window_end):
                    return False
            # Check interval
            if last_run and task.interval_hours:
                hours_since = (now - last_run).total_seconds() / 3600
                if hours_since < task.interval_hours:
                    return False
            return True

        return False

    async def _execute(self, task: HeartbeatTask, now: datetime):
        """Execute a heartbeat task."""
        logger.info("Executing heartbeat task: %s", task.name)

        if task.task_type == "news_digest":
            await self._run_news_digest()

        elif task.task_type == "quiet_channel":
            await self._run_quiet_check(task)

    async def _run_news_digest(self):
        """Run the news digest and post to the channel."""
        channel_id = await self._resolve_channel_id()
        if not channel_id:
            return

        async def post_message(text: str) -> str | None:
            try:
                result = await self.slack_client.chat_postMessage(
                    channel=channel_id, text=text
                )
                return result.get("ts")
            except Exception as e:
                logger.error("Failed to post news digest message: %s", e)
                return None

        async def post_thread_reply(thread_ts: str, text: str):
            try:
                await self.slack_client.chat_postMessage(
                    channel=channel_id, text=text, thread_ts=thread_ts
                )
            except Exception as e:
                logger.error("Failed to post news thread reply: %s", e)

        await run_news_digest(post_message, post_thread_reply)

    async def _run_quiet_check(self, task: HeartbeatTask):
        """Check if the channel has been quiet and post a conversation starter."""
        buf = self.buffers.get(CHANNEL_NAME)

        # If buffer exists and has recent messages, check timing
        if buf and buf.messages:
            last_msg = buf.messages[-1]
            try:
                last_ts = float(last_msg.timestamp)
                import time
                hours_quiet = (time.time() - last_ts) / 3600
                if hours_quiet < task.quiet_threshold_hours:
                    return  # Channel isn't quiet enough
            except (ValueError, TypeError):
                pass  # Can't parse timestamp, proceed anyway

        # Load personality and generate a conversation starter
        try:
            personality = PERSONALITY_PATH.read_text(encoding="utf-8").replace("{BOT_NAME}", BOT_NAME)
        except FileNotFoundError:
            personality = f"You are {BOT_NAME}, a witty Slack bot."

        messages = [
            {"role": "system", "content": personality},
            {
                "role": "user",
                "content": (
                    "The Slack channel has been quiet for a while. "
                    "Post a casual conversation starter, observation, or light roast "
                    "to get things going. Keep it short and natural — don't be try-hard about it."
                ),
            },
        ]

        response = await chat_completion(messages, temperature=0.9, max_tokens=256)

        if response:
            channel_id = await self._resolve_channel_id()
            if channel_id:
                try:
                    await self.slack_client.chat_postMessage(
                        channel=channel_id, text=response
                    )
                except Exception as e:
                    logger.error("Failed to post quiet channel message: %s", e)

    async def _resolve_channel_id(self) -> str | None:
        """Resolve CHANNEL_NAME to a Slack channel ID."""
        try:
            result = await self.slack_client.conversations_list(
                types="public_channel", limit=200
            )
            for ch in result.get("channels", []):
                if ch["name"] == CHANNEL_NAME:
                    return ch["id"]
            logger.error("Channel #%s not found", CHANNEL_NAME)
            return None
        except Exception as e:
            logger.error("Failed to resolve channel ID: %s", e)
            return None
