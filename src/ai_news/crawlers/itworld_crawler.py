"""ITWorld Korea RSS 크롤러 (AI 키워드 필터링)"""
import re
from datetime import datetime
from time import mktime
from typing import Optional

import feedparser

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import AI_KEYWORDS, CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class ITWorldCrawler(BaseCrawler):
    """ITWorld Korea RSS 크롤러 — AI 관련 기사만 필터링"""

    source = ArticleSource.ITWORLD
    name = "ITWorld Korea"

    def __init__(self):
        self.feed_url = CRAWL_SOURCES["itworld"]["feed_url"]
        self._keyword_pattern = re.compile(
            "|".join(re.escape(kw) for kw in AI_KEYWORDS),
            re.IGNORECASE,
        )

    async def crawl(self, max_items: int = 20) -> list[Article]:
        articles: list[Article] = []

        try:
            feed = feedparser.parse(self.feed_url)
            if feed.bozo and not feed.entries:
                logger.warning(f"ITWorld 피드 파싱 실패: {feed.bozo_exception}")
                return articles

            for entry in feed.entries:
                if len(articles) >= max_items:
                    break
                try:
                    article = self._parse_entry(entry)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"ITWorld 항목 파싱 실패: {e}")
                    continue

        except Exception as e:
            logger.error(f"ITWorld 크롤링 실패: {e}")

        logger.info(f"ITWorld 총 {len(articles)}건 수집 (AI 필터링 후)")
        return articles

    def _parse_entry(self, entry: dict) -> Optional[Article]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            return None

        summary = entry.get("summary", "") or entry.get("description", "")
        summary_text = re.sub(r"<[^>]+>", "", summary).strip()

        # AI 키워드 필터링
        search_text = f"{title} {summary_text}"
        if not self._keyword_pattern.search(search_text):
            return None

        article_id = entry.get("id", link)
        # itworld.co.kr/news/XXXXXX 패턴에서 ID 추출
        id_match = re.search(r"/news/(\d+)", link)
        if id_match:
            article_id = id_match.group(1)

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
            source=ArticleSource.ITWORLD,
            category=ArticleCategory.INDUSTRY_NEWS,
            title=title,
            url=link,
            authors=authors,
            published_at=published_at,
            summary=summary_text[:1000] if summary_text else None,
        )
