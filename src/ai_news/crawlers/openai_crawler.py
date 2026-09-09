"""OpenAI 블로그 크롤러 (RSS 우선, HTML 스크래핑 폴백)"""
import re
from datetime import datetime
from time import mktime
from typing import Optional

import feedparser
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
        """OpenAI 블로그에서 기사 수집 (RSS 우선)"""
        articles = self._crawl_rss(max_items)
        if articles:
            logger.info(f"OpenAI 총 {len(articles)}건 수집 (RSS)")
            return articles

        logger.warning("OpenAI RSS 수집 실패 - HTML 스크래핑 폴백")
        return self._crawl_html(max_items)

    def _crawl_rss(self, max_items: int) -> list[Article]:
        """뉴스 RSS 피드에서 수집

        HTML 페이지는 봇 차단(403)이라 RSS가 유일하게 동작하는 경로다.
        """
        articles: list[Article] = []
        url = self.config["rss_url"]

        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            if not feed.entries:
                logger.warning(f"OpenAI RSS가 비어 있음: {url}")
                return []

            seen_ids: set[str] = set()
            for entry in feed.entries:
                article = self._parse_feed_entry(entry)
                if not article or article.id in seen_ids:
                    continue
                seen_ids.add(article.id)
                articles.append(article)

                if len(articles) >= max_items:
                    break

        except Exception as e:
            logger.error(f"OpenAI RSS 크롤링 실패 ({url}): {e}")

        return articles

    def _parse_feed_entry(self, entry: dict) -> Optional[Article]:
        """RSS 항목을 Article로 변환"""
        title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
        link = entry.get("link", "")
        if not title or not link:
            return None

        # URL 마지막 경로를 ID로 사용 (기존 HTML 경로와 동일한 slug)
        slug = link.rstrip("/").split("/")[-1]
        if not slug:
            return None

        published_at = None
        if entry.get("published_parsed"):
            try:
                published_at = datetime.fromtimestamp(mktime(entry.published_parsed))
            except Exception:
                pass

        summary = re.sub(r"<[^>]+>", "", entry.get("summary", "") or "").strip()

        tags = ["openai"]
        for tag in entry.get("tags", []) or []:
            term = (tag.get("term") or "").strip()
            if term and term not in tags:
                tags.append(term)

        return Article(
            id=slug,
            source=ArticleSource.OPENAI,
            category=ArticleCategory.COMPANY_NEWS,
            title=title,
            url=link,
            published_at=published_at,
            summary=summary[:500] if summary else None,
            tags=tags,
        )

    def _crawl_html(self, max_items: int) -> list[Article]:
        """HTML 스크래핑 폴백 (봇 차단 시 빈 목록 반환)"""
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
                # `if e.response`는 Response.__bool__이 status 4xx/5xx에서 False라
                # 403도 else로 빠져 ERROR로 기록됐다. is not None으로 비교해야 한다.
                if e.response is not None and e.response.status_code == 403:
                    logger.warning(f"OpenAI {url} 접근 차단 (403). 다음 URL 시도...")
                    continue
                else:
                    logger.error(f"OpenAI 크롤링 실패: {e}")
            except Exception as e:
                logger.error(f"OpenAI 크롤링 실패 ({url}): {e}")

        logger.info(f"OpenAI 총 {len(articles)}건 수집 (HTML)")
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
