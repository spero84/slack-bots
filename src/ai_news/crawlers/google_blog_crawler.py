"""Google AI Blog RSS 크롤러"""
import re
from datetime import datetime
from time import mktime
from typing import Optional

import feedparser

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class GoogleBlogCrawler(BaseCrawler):
    """Google AI Blog RSS 크롤러"""

    source = ArticleSource.GOOGLE_BLOG
    name = "Google AI Blog"

    def __init__(self):
        self.feed_url = CRAWL_SOURCES["google_blog"]["feed_url"]

    async def crawl(self, max_items: int = 20) -> list[Article]:
        articles: list[Article] = []

        try:
            feed = feedparser.parse(self.feed_url)
            if feed.bozo and not feed.entries:
                logger.warning(f"Google AI Blog 피드 파싱 실패: {feed.bozo_exception}")
                return articles

            for entry in feed.entries[:max_items]:
                try:
                    article = self._parse_entry(entry)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"Google AI Blog 항목 파싱 실패: {e}")
                    continue

        except Exception as e:
            logger.error(f"Google AI Blog 크롤링 실패: {e}")

        logger.info(f"Google AI Blog 총 {len(articles)}건 수집")
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

        authors = None
        if entry.get("author"):
            authors = [entry["author"].strip()]

        summary = entry.get("summary", "") or entry.get("description", "")
        summary = re.sub(r"<[^>]+>", "", summary).strip()

        tags = []
        if entry.get("tags"):
            tags = [tag.get("term", "") for tag in entry.tags if tag.get("term")]

        return Article(
            id=article_id,
            source=ArticleSource.GOOGLE_BLOG,
            category=ArticleCategory.COMPANY_NEWS,
            title=title,
            url=link,
            authors=authors,
            published_at=published_at,
            summary=summary[:1000] if summary else None,
            tags=tags,
        )
