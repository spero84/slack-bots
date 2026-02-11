"""TechCrunch AI RSS 크롤러"""
import re
from datetime import datetime
from time import mktime
from typing import Optional

import feedparser

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class TechCrunchCrawler(BaseCrawler):
    """TechCrunch AI 카테고리 RSS 크롤러"""

    source = ArticleSource.TECHCRUNCH
    name = "TechCrunch"

    def __init__(self):
        self.feed_url = CRAWL_SOURCES["techcrunch"]["feed_url"]

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """TechCrunch AI RSS 피드에서 기사 수집"""
        articles: list[Article] = []

        try:
            feed = feedparser.parse(self.feed_url)
            if feed.bozo and not feed.entries:
                logger.warning(f"TechCrunch 피드 파싱 실패: {feed.bozo_exception}")
                return articles

            for entry in feed.entries[:max_items]:
                try:
                    article = self._parse_entry(entry)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"TechCrunch 항목 파싱 실패: {e}")
                    continue

        except Exception as e:
            logger.error(f"TechCrunch 크롤링 실패: {e}")

        logger.info(f"TechCrunch 총 {len(articles)}건 수집")
        return articles

    def _parse_entry(self, entry: dict) -> Optional[Article]:
        """RSS 항목을 Article로 변환"""
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            return None

        # 고유 ID: URL 슬러그 또는 guid
        article_id = entry.get("id", link)
        # URL에서 슬러그 추출
        slug_match = re.search(r"techcrunch\.com/\d{4}/\d{2}/\d{2}/(.+?)/?$", link)
        if slug_match:
            article_id = slug_match.group(1)

        # 게시일
        published_at = None
        if entry.get("published_parsed"):
            try:
                published_at = datetime.fromtimestamp(mktime(entry.published_parsed))
            except Exception:
                pass

        # 저자
        authors = None
        if entry.get("author"):
            authors = [entry["author"].strip()]

        # 요약 (HTML 태그 제거)
        summary = entry.get("summary", "") or entry.get("description", "")
        summary = re.sub(r"<[^>]+>", "", summary).strip()

        # 태그
        tags = []
        if entry.get("tags"):
            tags = [tag.get("term", "") for tag in entry.tags if tag.get("term")]

        return Article(
            id=article_id,
            source=ArticleSource.TECHCRUNCH,
            category=ArticleCategory.INDUSTRY_NEWS,
            title=title,
            url=link,
            authors=authors,
            published_at=published_at,
            summary=summary[:1000] if summary else None,
            tags=tags,
        )
