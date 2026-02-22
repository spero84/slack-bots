"""arXiv RSS 크롤러"""
import re
from datetime import datetime
from typing import Optional

import feedparser

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import ARXIV_CATEGORIES, CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class ArxivCrawler(BaseCrawler):
    """arXiv RSS 피드 크롤러 (cs.AI, cs.CL, cs.CV, cs.LG)"""

    source = ArticleSource.ARXIV
    name = "arXiv"

    def __init__(self):
        self.feeds = CRAWL_SOURCES["arxiv"]["feeds"]

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """arXiv RSS 피드에서 최신 논문 수집"""
        articles: list[Article] = []
        seen_ids: set[str] = set()

        for category, feed_url in self.feeds.items():
            try:
                feed = feedparser.parse(feed_url)
                if feed.bozo and not feed.entries:
                    logger.warning(f"arXiv {category} 피드 파싱 실패: {feed.bozo_exception}")
                    continue

                for entry in feed.entries:
                    try:
                        article = self._parse_entry(entry, category)
                        if article and article.id not in seen_ids:
                            seen_ids.add(article.id)
                            articles.append(article)
                    except Exception as e:
                        logger.warning(f"arXiv 항목 파싱 실패: {e}")
                        continue

                logger.info(f"arXiv {category}: {len(feed.entries)}건 파싱")

            except Exception as e:
                logger.error(f"arXiv {category} 크롤링 실패: {e}")
                continue

        # 최신순 정렬 후 제한
        articles.sort(key=lambda a: a.published_at or datetime.min, reverse=True)
        articles = articles[:max_items]

        logger.info(f"arXiv 총 {len(articles)}건 수집")
        return articles

    def _parse_entry(self, entry: dict, category: str) -> Optional[Article]:
        """RSS 항목을 Article로 변환"""
        link = entry.get("link", "")
        arxiv_id = self._extract_arxiv_id(link)
        if not arxiv_id:
            return None

        title = entry.get("title", "").strip()
        # arXiv RSS 제목에서 카테고리 접두어 제거 (예: "cs.AI: Title")
        title = re.sub(r"^[\w.]+:\s*", "", title)
        # 줄바꿈 제거
        title = re.sub(r"\s+", " ", title).strip()

        if not title:
            return None

        # 저자 추출
        authors = None
        author_str = entry.get("author", "") or entry.get("dc_creator", "")
        if author_str:
            authors = [a.strip() for a in author_str.split(",") if a.strip()]

        # 초록 (description에서 HTML 태그 제거)
        summary = entry.get("summary", "") or entry.get("description", "")
        summary = re.sub(r"<[^>]+>", "", summary).strip()
        # arXiv RSS summary에서 "arXiv:XXXX.XXXXX" 앞부분 제거
        summary = re.sub(r"^arXiv:\d+\.\d+v?\d*\s*", "", summary).strip()

        # 게시일
        published_at = None
        if entry.get("published_parsed"):
            try:
                from time import mktime
                published_at = datetime.fromtimestamp(mktime(entry.published_parsed))
            except Exception:
                pass
        elif entry.get("updated_parsed"):
            try:
                from time import mktime
                published_at = datetime.fromtimestamp(mktime(entry.updated_parsed))
            except Exception:
                pass

        # 태그 추출
        tags = [category]
        if entry.get("tags"):
            for tag in entry.tags:
                term = tag.get("term", "")
                if term and term not in tags:
                    tags.append(term)

        return Article(
            id=arxiv_id,
            source=ArticleSource.ARXIV,
            category=ArticleCategory.PAPER,
            title=title,
            url=f"https://arxiv.org/abs/{arxiv_id}",
            authors=authors,
            published_at=published_at,
            summary=summary[:2000] if summary else None,
            tags=tags,
        )

    def _extract_arxiv_id(self, link: str) -> Optional[str]:
        """URL에서 arXiv ID 추출"""
        match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", link)
        if match:
            return match.group(1)
        # 구형 ID 형식
        match = re.search(r"abs/([a-z-]+/\d+)", link)
        if match:
            return match.group(1)
        return None
