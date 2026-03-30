import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine

import feedparser
import yaml

from config import NEWS_SUMMARY_PATH
from llm import chat_completion

logger = logging.getLogger(__name__)


def _load_digests() -> list[dict]:
    """Load digest definitions from news_summary.yaml."""
    try:
        text = NEWS_SUMMARY_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        return data.get("digests", [])
    except (FileNotFoundError, yaml.YAMLError) as e:
        logger.error("Failed to load news_summary.yaml: %s", e)
        return []


def _parse_pub_date(entry: dict) -> datetime | None:
    """Extract a datetime from a feedparser entry."""
    for field in ("published_parsed", "updated_parsed"):
        t = entry.get(field)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def _fetch_recent_articles(feeds: list[str], hours: int = 24) -> list[dict]:
    """Fetch articles from RSS feeds published within the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles = []

    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
            for entry in parsed.entries:
                pub_date = _parse_pub_date(entry)

                # If no date, include it (fall back to entry order)
                if pub_date and pub_date < cutoff:
                    continue

                articles.append({
                    "title": entry.get("title", "Untitled"),
                    "url": entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                    "pub_date": pub_date,
                    "source": feed_url,
                })
        except Exception as e:
            logger.warning("Failed to fetch feed %s: %s", feed_url, e)

    return articles


def _deduplicate(articles: list[dict]) -> list[dict]:
    """Remove duplicate articles by URL and similar titles."""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    unique = []

    for article in articles:
        url = article["url"]
        title_key = article["title"].lower().strip()

        if url in seen_urls or title_key in seen_titles:
            continue

        seen_urls.add(url)
        seen_titles.add(title_key)
        unique.append(article)

    return unique


async def _rank_articles(articles: list[dict], relevance: str, max_articles: int) -> list[dict]:
    """Use the LLM to rank articles by relevance and generate short descriptions."""
    article_list = "\n".join(
        f"{i+1}. [{a['title']}]({a['url']})\n   {a['summary'][:200]}"
        for i, a in enumerate(articles[:30])  # cap input to avoid blowing context
    )

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "You are a news curator. Given a list of articles and relevance criteria, "
                "pick the top articles and write a short 1-2 sentence description for each. "
                "Reply with JSON only: [{\"index\": 1, \"description\": \"...\"}]"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Relevance criteria:\n{relevance}\n\n"
                f"Articles:\n{article_list}\n\n"
                f"Pick the top {max_articles} most relevant articles. Return JSON array."
            ),
        },
    ]

    response = await chat_completion(prompt_messages, temperature=0.3, max_tokens=1024)

    try:
        import json
        ranked = json.loads(response)
        results = []
        for item in ranked[:max_articles]:
            idx = item.get("index", 0) - 1
            if 0 <= idx < len(articles):
                results.append({
                    **articles[idx],
                    "description": item.get("description", articles[idx]["title"]),
                })
        return results
    except Exception as e:
        logger.warning("Failed to parse LLM ranking response: %s", e)
        # Fall back to first N articles with their summaries as descriptions
        return [
            {**a, "description": a["summary"][:200] or a["title"]}
            for a in articles[:max_articles]
        ]


async def run_news_digest(
    post_message: Callable[[str], Coroutine[Any, Any, Any]],
    post_thread_reply: Callable[..., Coroutine[Any, Any, None]],
) -> None:
    """Run all configured news digests.

    Args:
        post_message: Async callable that posts a message and returns a parent reference
                      (Discord Message object, Slack timestamp, etc.)
        post_thread_reply: Async callable(parent_ref, text) that posts a threaded reply.
    """
    digests = _load_digests()

    for digest in digests:
        title = digest.get("title", "News")
        feeds = digest.get("feeds", [])
        relevance = digest.get("relevance", "")
        max_articles = digest.get("max_articles", 5)

        if not feeds:
            continue

        articles = _fetch_recent_articles(feeds)
        articles = _deduplicate(articles)

        if not articles:
            logger.info("No recent articles found for digest: %s", title)
            continue

        ranked = await _rank_articles(articles, relevance, max_articles)

        # Post parent message
        today = datetime.now(timezone.utc).strftime("%B %d, %Y")
        parent = await post_message(f"\U0001f5de\ufe0f {title}, {today}")

        if not parent:
            logger.error("Failed to post parent message for digest: %s", title)
            continue

        # Post threaded replies
        for i, article in enumerate(ranked, 1):
            text = f"{i}. {article['description']}\n{article['url']}"
            await post_thread_reply(parent, text)
