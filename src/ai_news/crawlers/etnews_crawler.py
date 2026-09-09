"""전자신문 (ETNews) 크롤러 (섹션 RSS 우선, HTML 스크래핑 폴백)"""
import hashlib
import re
from datetime import datetime
from time import mktime
from typing import Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import AI_KEYWORDS, CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class ETNewsCrawler(BaseCrawler):
    """전자신문 크롤러 — AI 관련 기사만 필터링"""

    source = ArticleSource.ETNEWS
    name = "ETNews"

    def __init__(self):
        self.config = CRAWL_SOURCES["etnews"]
        self.feeds = self.config["feeds"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
        self._keyword_pattern = re.compile(
            "|".join(re.escape(kw) for kw in AI_KEYWORDS),
            re.IGNORECASE,
        )

    async def crawl(self, max_items: int = 20) -> list[Article]:
        articles = self._crawl_feeds(max_items)
        if articles:
            logger.info(f"ETNews 총 {len(articles)}건 수집 (RSS)")
            return articles

        logger.warning("ETNews RSS 수집 실패 - HTML 스크래핑 폴백")
        return self._crawl_html(max_items)

    def _crawl_feeds(self, max_items: int) -> list[Article]:
        """섹션 RSS에서 수집 (AI 섹션 + SW 섹션)

        기존의 `/news/article_list/AI` 경로는 404다. 섹션 RSS는 본문 요약과
        게시일까지 제공하므로 HTML 목록 스크래핑보다 정확하다.
        """
        articles: list[Article] = []
        seen_urls: set[str] = set()

        for feed_url, filter_ai in self.feeds:
            if len(articles) >= max_items:
                break

            try:
                resp = self.session.get(feed_url, timeout=15)
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)

                if not feed.entries:
                    logger.warning(f"ETNews RSS가 비어 있음: {feed_url}")
                    continue

                for entry in feed.entries:
                    if len(articles) >= max_items:
                        break

                    title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
                    url = entry.get("link", "")
                    if not title or not url or url in seen_urls:
                        continue

                    # SW 섹션은 AI 외 기사도 섞여 있어 키워드로 걸러낸다
                    summary = re.sub(
                        r"<[^>]+>", "", entry.get("summary", "") or ""
                    ).strip()
                    if filter_ai and not self._keyword_pattern.search(
                        f"{title} {summary}"
                    ):
                        continue

                    published_at = None
                    if entry.get("published_parsed"):
                        try:
                            published_at = datetime.fromtimestamp(
                                mktime(entry.published_parsed)
                            )
                        except Exception:
                            pass

                    article = self._build_article(title, url, summary, published_at)
                    if article:
                        seen_urls.add(url)
                        articles.append(article)

            except Exception as e:
                logger.warning(f"ETNews RSS 실패 ({feed_url}): {e}")
                continue

        return articles

    def _crawl_html(self, max_items: int) -> list[Article]:
        """메인 페이지 HTML 스크래핑 폴백 (AI 키워드 필터링)"""
        articles: list[Article] = []

        try:
            resp = self.session.get(self.config["url"], timeout=15)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            for title, url, summary in self._find_article_links(soup, filter_ai=True):
                if len(articles) >= max_items:
                    break
                article = self._build_article(title, url, summary)
                if article:
                    articles.append(article)

        except Exception as e:
            logger.warning(f"ETNews HTML 크롤링 실패: {e}")

        logger.info(f"ETNews 총 {len(articles)}건 수집 (HTML)")
        return articles

    def _find_article_links(self, soup: BeautifulSoup, filter_ai: bool = False) -> list[tuple[str, str, Optional[str]]]:
        results = []

        # ETNews 기사 링크 패턴: /YYYYMMDDXXXXXX 형태
        for link in soup.find_all("a", href=re.compile(r"/\d{14}")):
            title = link.get_text(strip=True)
            if not title or len(title) < 10:
                continue
            href = link.get("href", "")
            url = href if href.startswith("http") else f"https://www.etnews.com{href}"

            if filter_ai and not self._keyword_pattern.search(title):
                continue

            results.append((title, url, None))

        # 중복 제거
        seen = set()
        unique = []
        for title, url, summary in results:
            if url not in seen:
                seen.add(url)
                unique.append((title, url, summary))
        return unique

    def _build_article(
        self,
        title: str,
        url: str,
        summary: Optional[str],
        published_at: Optional[datetime] = None,
    ) -> Optional[Article]:
        id_match = re.search(r"/(\d{14})", url)
        if id_match:
            article_id = id_match.group(1)
        else:
            article_id = hashlib.md5(url.encode()).hexdigest()[:12]

        return Article(
            id=article_id,
            source=ArticleSource.ETNEWS,
            category=ArticleCategory.INDUSTRY_NEWS,
            title=title,
            url=url,
            published_at=published_at,
            summary=summary[:500] if summary else None,
            tags=["etnews"],
        )

    async def close(self):
        self.session.close()
