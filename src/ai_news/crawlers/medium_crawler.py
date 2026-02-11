"""Medium AI 태그 RSS 크롤러"""
import re
from datetime import datetime
from time import mktime
from typing import Optional

import feedparser

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import AI_KEYWORDS, CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class MediumCrawler(BaseCrawler):
    """Medium AI 태그 RSS 크롤러"""

    source = ArticleSource.MEDIUM
    name = "Medium AI"

    def __init__(self):
        self.feed_url = CRAWL_SOURCES["medium"]["feed_url"]
        self._keyword_pattern = re.compile(
            "|".join(re.escape(kw) for kw in AI_KEYWORDS),
            re.IGNORECASE,
        )

    async def crawl(self, max_items: int = 20) -> list[Article]:
        articles: list[Article] = []

        try:
            feed = feedparser.parse(self.feed_url)
            if feed.bozo and not feed.entries:
                logger.warning(f"Medium 피드 파싱 실패: {feed.bozo_exception}")
                return articles

            for entry in feed.entries:
                if len(articles) >= max_items:
                    break
                try:
                    article = self._parse_entry(entry)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"Medium 항목 파싱 실패: {e}")
                    continue

        except Exception as e:
            logger.error(f"Medium 크롤링 실패: {e}")

        logger.info(f"Medium AI 총 {len(articles)}건 수집")
        return articles

    def _parse_entry(self, entry: dict) -> Optional[Article]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            return None

        # Medium 링크에서 쿼리 파라미터 제거
        link = link.split("?")[0]

        summary = entry.get("summary", "") or entry.get("description", "")
        summary_text = re.sub(r"<[^>]+>", "", summary).strip()

        tags = []
        if entry.get("tags"):
            tags = [tag.get("term", "") for tag in entry.tags if tag.get("term")]

        # AI 키워드 필터링 (Medium AI 태그라도 관련없는 글 존재)
        search_text = f"{title} {summary_text} {' '.join(tags)}"
        if not self._keyword_pattern.search(search_text):
            return None

        # ID: Medium 포스트 ID (URL 마지막 세그먼트의 해시)
        article_id = entry.get("id", link)
        slug_match = re.search(r"-([a-f0-9]+)$", link)
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

        return Article(
            id=article_id,
            source=ArticleSource.MEDIUM,
            category=ArticleCategory.INDUSTRY_NEWS,
            title=title,
            url=link,
            authors=authors,
            published_at=published_at,
            summary=summary_text[:1000] if summary_text else None,
            tags=tags,
        )
