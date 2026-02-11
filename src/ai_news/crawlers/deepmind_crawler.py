"""Google DeepMind 블로그 크롤러"""
import hashlib
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class DeepMindCrawler(BaseCrawler):
    """Google DeepMind 블로그 크롤러"""

    source = ArticleSource.DEEPMIND
    name = "Google DeepMind"

    def __init__(self):
        self.config = CRAWL_SOURCES["deepmind"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """DeepMind 블로그에서 기사 수집"""
        articles: list[Article] = []

        try:
            resp = self.session.get(self.config["blog_url"], timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # DeepMind 블로그 포스트 링크: /blog/ 패턴 (내부 링크)
            links = soup.find_all("a", href=re.compile(r"^/blog/[^/]+/?$"))

            seen_ids: set[str] = set()
            for link in links:
                href = link.get("href", "")
                slug = href.strip("/").split("/")[-1]
                if not slug or slug in seen_ids:
                    continue
                seen_ids.add(slug)

                article = self._parse_card(link, href, slug)
                if article:
                    articles.append(article)

                if len(articles) >= max_items:
                    break

        except Exception as e:
            logger.error(f"DeepMind 크롤링 실패: {e}")

        logger.info(f"DeepMind 총 {len(articles)}건 수집")
        return articles

    def _parse_card(self, element, href: str, slug: str) -> Optional[Article]:
        """카드에서 기사 정보 추출"""
        title = ""

        heading = element.find(["h2", "h3", "h4"])
        if heading:
            title = heading.get_text(strip=True)
        else:
            text = element.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if lines:
                title = max(lines, key=len)

        if not title or len(title) < 5:
            return None

        full_url = f"https://deepmind.google{href}"

        # 날짜
        published_at = None
        time_tag = element.find("time")
        if time_tag:
            date_str = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
            published_at = self._parse_date(date_str)

        # 카테고리 태그
        tags = ["deepmind"]
        span_tags = element.find_all("span")
        for span in span_tags:
            text = span.get_text(strip=True)
            if text and len(text) < 30 and text.lower() not in ["read more", "blog"]:
                if re.match(r"^[A-Za-z\s&]+$", text) and len(text) > 2:
                    tags.append(text.lower())

        # 요약
        summary = None
        p_tag = element.find("p")
        if p_tag:
            summary = p_tag.get_text(strip=True)

        return Article(
            id=slug,
            source=ArticleSource.DEEPMIND,
            category=ArticleCategory.COMPANY_NEWS,
            title=title,
            url=full_url,
            published_at=published_at,
            summary=summary[:500] if summary else None,
            tags=tags,
        )

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        from dateutil import parser as dateutil_parser
        try:
            return dateutil_parser.parse(date_str, fuzzy=True)
        except Exception:
            return None

    async def close(self):
        self.session.close()
