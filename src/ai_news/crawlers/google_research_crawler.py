"""Google Research Blog RSS 크롤러"""
import re
from datetime import datetime
from time import mktime
from typing import Optional

import feedparser

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class GoogleResearchCrawler(BaseCrawler):
    """Google Research Blog RSS 크롤러"""

    source = ArticleSource.GOOGLE_RESEARCH
    name = "Google Research"

    def __init__(self):
        self.feed_url = CRAWL_SOURCES["google_research"]["feed_url"]

    async def crawl(self, max_items: int = 20) -> list[Article]:
        articles: list[Article] = []

        try:
            feed = feedparser.parse(self.feed_url)
            if feed.bozo and not feed.entries:
                logger.warning(f"Google Research 피드 파싱 실패: {feed.bozo_exception}")
                return articles

            for entry in feed.entries[:max_items]:
                try:
                    article = self._parse_entry(entry)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"Google Research 항목 파싱 실패: {e}")
                    continue

        except Exception as e:
            logger.error(f"Google Research 크롤링 실패: {e}")

        logger.info(f"Google Research 총 {len(articles)}건 수집")
        return articles

    def _parse_entry(self, entry: dict) -> Optional[Article]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            return None

        article_id = entry.get("id", link)
        slug_match = re.search(r"/([^/]+)/?$", link)
        if slug_match:
            article_id = slug_match.group(1)

        published_at = None
        if entry.get("published_parsed"):
            try:
                published_at = datetime.fromtimestamp(mktime(entry.published_parsed))
            except Exception:
                pass

        authors = []
        if entry.get("authors"):
            authors = [a.get("name", "") for a in entry.authors if a.get("name")]
        elif entry.get("author"):
            authors = [entry["author"].strip()]

        summary = entry.get("summary", "") or entry.get("description", "")
        summary = re.sub(r"<[^>]+>", "", summary).strip()

        tags = []
        if entry.get("tags"):
            tags = [tag.get("term", "") for tag in entry.tags if tag.get("term")]

        return Article(
            id=article_id,
            source=ArticleSource.GOOGLE_RESEARCH,
            category=ArticleCategory.PAPER,
            title=title,
            url=link,
            authors=authors or None,
            published_at=published_at,
            summary=summary[:1000] if summary else None,
            tags=tags,
        )
