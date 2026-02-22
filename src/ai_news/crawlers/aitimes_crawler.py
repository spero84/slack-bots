"""AI Times 크롤러 (HTML 스크래핑)"""
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


class AITimesCrawler(BaseCrawler):
    """AI Times 크롤러 — AI 전문 매체이므로 키워드 필터 불필요"""

    source = ArticleSource.AITIMES
    name = "AI Times"

    def __init__(self):
        self.config = CRAWL_SOURCES["aitimes"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    async def crawl(self, max_items: int = 20) -> list[Article]:
        articles: list[Article] = []

        try:
            resp = self.session.get(self.config["url"], timeout=15)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "lxml")

            # AI Times 기사 목록 파싱
            # 일반적인 뉴스 사이트 패턴: article 태그 또는 기사 목록 컨테이너
            items = self._find_article_links(soup)

            for title, url, summary in items[:max_items]:
                try:
                    article = self._build_article(title, url, summary)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"AI Times 항목 파싱 실패: {e}")

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                logger.warning("AI Times 403 Forbidden - User-Agent 차단 가능성")
            else:
                logger.error(f"AI Times 크롤링 실패: {e}")
        except Exception as e:
            logger.error(f"AI Times 크롤링 실패: {e}")

        logger.info(f"AI Times 총 {len(articles)}건 수집")
        return articles

    def _find_article_links(self, soup: BeautifulSoup) -> list[tuple[str, str, Optional[str]]]:
        """기사 제목, URL, 요약 추출"""
        results = []

        # 패턴 1: auto_article 클래스 (AI Times 일반 패턴)
        articles_divs = soup.select("div.article-list-content, div.auto_article, ul.type2 li")
        if articles_divs:
            for div in articles_divs:
                link = div.find("a", href=True)
                if not link:
                    continue
                href = link.get("href", "")
                if not href or href == "#":
                    continue

                title_tag = div.find(["h2", "h3", "h4", "strong"]) or link
                title = title_tag.get_text(strip=True)
                if not title or len(title) < 5:
                    continue

                url = href if href.startswith("http") else f"https://www.aitimes.com{href}"
                summary = None
                p_tag = div.find("p")
                if p_tag:
                    summary = p_tag.get_text(strip=True)

                results.append((title, url, summary))

        # 패턴 2: 일반 a 태그에서 기사 링크 추출
        if not results:
            for link in soup.find_all("a", href=re.compile(r"/news/articleView\.html\?idxno=\d+")):
                title = link.get_text(strip=True)
                if not title or len(title) < 10:
                    continue
                href = link.get("href", "")
                url = href if href.startswith("http") else f"https://www.aitimes.com{href}"
                results.append((title, url, None))

        # 중복 URL 제거
        seen = set()
        unique = []
        for title, url, summary in results:
            if url not in seen:
                seen.add(url)
                unique.append((title, url, summary))
        return unique

    def _build_article(self, title: str, url: str, summary: Optional[str]) -> Optional[Article]:
        # ID: URL에서 idxno 추출
        id_match = re.search(r"idxno=(\d+)", url)
        if id_match:
            article_id = id_match.group(1)
        else:
            article_id = hashlib.md5(url.encode()).hexdigest()[:12]

        return Article(
            id=article_id,
            source=ArticleSource.AITIMES,
            category=ArticleCategory.INDUSTRY_NEWS,
            title=title,
            url=url,
            summary=summary[:500] if summary else None,
            tags=["ai", "aitimes"],
        )

    async def close(self):
        self.session.close()
