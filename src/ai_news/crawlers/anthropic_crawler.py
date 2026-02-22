"""Anthropic 블로그 크롤러"""
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


class AnthropicCrawler(BaseCrawler):
    """Anthropic 뉴스/연구 블로그 크롤러"""

    source = ArticleSource.ANTHROPIC
    name = "Anthropic"

    def __init__(self):
        self.config = CRAWL_SOURCES["anthropic"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; AI-News-Bot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """Anthropic 뉴스 + 연구 페이지에서 기사 수집"""
        articles: list[Article] = []
        seen_ids: set[str] = set()

        for page_url in [self.config["news_url"], self.config["research_url"]]:
            try:
                items = self._crawl_page(page_url)
                for article in items:
                    if article.id not in seen_ids:
                        seen_ids.add(article.id)
                        articles.append(article)
            except Exception as e:
                logger.error(f"Anthropic {page_url} 크롤링 실패: {e}")

        articles.sort(key=lambda a: a.published_at or datetime.min, reverse=True)
        articles = articles[:max_items]

        logger.info(f"Anthropic 총 {len(articles)}건 수집")
        return articles

    def _crawl_page(self, page_url: str) -> list[Article]:
        """페이지에서 기사 목록 파싱"""
        articles = []

        try:
            resp = self.session.get(page_url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            # Anthropic 블로그 카드 링크 파싱
            # 일반적으로 <a> 태그에 href="/news/..." 또는 "/research/..." 패턴
            links = soup.find_all("a", href=re.compile(r"^/(news|research)/[^/]+"))

            seen_hrefs: set[str] = set()
            for link in links:
                href = link.get("href", "")
                if href in seen_hrefs:
                    continue
                seen_hrefs.add(href)

                article = self._parse_card(link, href)
                if article:
                    articles.append(article)

        except Exception as e:
            logger.error(f"Anthropic 페이지 파싱 실패 ({page_url}): {e}")

        return articles

    def _parse_card(self, element, href: str) -> Optional[Article]:
        """카드 요소에서 기사 정보 추출"""
        # 제목: 가장 긴 텍스트 또는 특정 태그
        title = ""

        # h2, h3 태그에서 제목 추출 시도
        heading = element.find(["h2", "h3", "h4"])
        if heading:
            title = heading.get_text(strip=True)
        else:
            # 전체 텍스트에서 가장 긴 줄
            text = element.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if lines:
                title = max(lines, key=len)

        if not title or len(title) < 5:
            return None

        # URL
        full_url = f"https://www.anthropic.com{href}"

        # ID: URL 슬러그
        slug = href.rstrip("/").split("/")[-1]
        article_id = slug or hashlib.md5(href.encode()).hexdigest()[:12]

        # 날짜 추출 시도 (time 태그 또는 날짜 패턴 텍스트)
        published_at = None
        time_tag = element.find("time")
        if time_tag:
            date_str = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
            published_at = self._parse_date(date_str)
        else:
            # 텍스트에서 날짜 패턴 탐색
            text = element.get_text()
            date_match = re.search(
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+\d{1,2},?\s+\d{4}",
                text,
            )
            if date_match:
                published_at = self._parse_date(date_match.group())

        # 요약 추출 (p 태그)
        summary = None
        p_tag = element.find("p")
        if p_tag:
            summary = p_tag.get_text(strip=True)

        return Article(
            id=article_id,
            source=ArticleSource.ANTHROPIC,
            category=ArticleCategory.COMPANY_NEWS,
            title=title,
            url=full_url,
            published_at=published_at,
            summary=summary[:500] if summary else None,
            tags=["anthropic"],
        )

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """다양한 날짜 형식 파싱"""
        from dateutil import parser as dateutil_parser
        try:
            return dateutil_parser.parse(date_str, fuzzy=True)
        except Exception:
            return None

    async def close(self):
        self.session.close()
