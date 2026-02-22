"""OpenAI 블로그 크롤러"""
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


class OpenAICrawler(BaseCrawler):
    """OpenAI 블로그 크롤러"""

    source = ArticleSource.OPENAI
    name = "OpenAI"

    def __init__(self):
        self.config = CRAWL_SOURCES["openai"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """OpenAI 블로그에서 기사 수집"""
        articles: list[Article] = []

        # 여러 URL 패턴 시도
        urls_to_try = [
            self.config["blog_url"],
            "https://openai.com/blog/",
            "https://openai.com/research/",
        ]

        for url in urls_to_try:
            try:
                resp = self.session.get(url, timeout=15)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")

                # /index/ 하위 링크 패턴
                links = soup.find_all("a", href=re.compile(r"^/index/[^/]+/?$"))

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

                if articles:
                    break  # 성공하면 중단

            except requests.exceptions.HTTPError as e:
                if e.response and e.response.status_code == 403:
                    logger.warning(f"OpenAI {url} 접근 차단 (403). 다음 URL 시도...")
                    continue
                else:
                    logger.error(f"OpenAI 크롤링 실패: {e}")
            except Exception as e:
                logger.error(f"OpenAI 크롤링 실패 ({url}): {e}")

        logger.info(f"OpenAI 총 {len(articles)}건 수집")
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

        full_url = f"https://openai.com{href}"

        # 날짜
        published_at = None
        time_tag = element.find("time")
        if time_tag:
            date_str = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
            published_at = self._parse_date(date_str)
        else:
            text = element.get_text()
            date_match = re.search(
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+\d{1,2},?\s+\d{4}",
                text,
            )
            if date_match:
                published_at = self._parse_date(date_match.group())

        # 요약
        summary = None
        p_tag = element.find("p")
        if p_tag:
            summary = p_tag.get_text(strip=True)

        return Article(
            id=slug,
            source=ArticleSource.OPENAI,
            category=ArticleCategory.COMPANY_NEWS,
            title=title,
            url=full_url,
            published_at=published_at,
            summary=summary[:500] if summary else None,
            tags=["openai"],
        )

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        from dateutil import parser as dateutil_parser
        try:
            return dateutil_parser.parse(date_str, fuzzy=True)
        except Exception:
            return None

    async def close(self):
        self.session.close()
