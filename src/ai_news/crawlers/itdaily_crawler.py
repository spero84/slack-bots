"""IT Daily 크롤러 (HTML 스크래핑, AI 키워드 필터링)"""
import hashlib
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import AI_KEYWORDS, CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class ITDailyCrawler(BaseCrawler):
    """IT Daily 크롤러 — AI 관련 기사만 필터링"""

    source = ArticleSource.ITDAILY
    name = "IT Daily"

    def __init__(self):
        self.config = CRAWL_SOURCES["itdaily"]
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
        articles: list[Article] = []

        try:
            resp = self.session.get(self.config["url"], timeout=15)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            items = self._find_article_links(soup)
            for title, url, summary in items[:max_items]:
                article = self._build_article(title, url, summary)
                if article:
                    articles.append(article)

        except Exception as e:
            logger.error(f"IT Daily 크롤링 실패: {e}")

        logger.info(f"IT Daily 총 {len(articles)}건 수집")
        return articles

    def _find_article_links(self, soup: BeautifulSoup) -> list[tuple[str, str, Optional[str]]]:
        results = []

        # IT Daily 기사 링크 패턴: /news/articleView.html?idxno=XXXXX
        for link in soup.find_all("a", href=re.compile(r"articleView\.html\?idxno=\d+")):
            title = link.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # AI 키워드 필터링
            if not self._keyword_pattern.search(title):
                continue

            href = link.get("href", "")
            url = href if href.startswith("http") else f"http://www.itdaily.kr{href}"

            results.append((title, url, None))

        # 중복 제거
        seen = set()
        unique = []
        for title, url, summary in results:
            if url not in seen:
                seen.add(url)
                unique.append((title, url, summary))
        return unique

    def _build_article(self, title: str, url: str, summary: Optional[str]) -> Optional[Article]:
        id_match = re.search(r"idxno=(\d+)", url)
        if id_match:
            article_id = id_match.group(1)
        else:
            article_id = hashlib.md5(url.encode()).hexdigest()[:12]

        return Article(
            id=article_id,
            source=ArticleSource.ITDAILY,
            category=ArticleCategory.INDUSTRY_NEWS,
            title=title,
            url=url,
            summary=summary[:500] if summary else None,
            tags=["itdaily"],
        )

    async def close(self):
        self.session.close()
