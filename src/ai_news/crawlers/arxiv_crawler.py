"""arXiv RSS 크롤러"""
import re
from datetime import datetime
from itertools import zip_longest
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

    # 신규 발표(new)와 타 분야 교차등록(cross)만 수집한다.
    # replace/replace-cross는 이미 발표된 논문의 개정판이라 뉴스 가치가 없다.
    ANNOUNCE_TYPES = ("new", "cross")

    def __init__(self):
        self.feeds = CRAWL_SOURCES["arxiv"]["feeds"]

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """arXiv RSS 피드에서 최신 논문 수집"""
        seen_ids: set[str] = set()
        per_category: list[list[Article]] = []

        for category, feed_url in self.feeds.items():
            try:
                feed = feedparser.parse(feed_url)
                if feed.bozo and not feed.entries:
                    logger.warning(f"arXiv {category} 피드 파싱 실패: {feed.bozo_exception}")
                    continue

                # 빈 피드는 정상 응답으로 위장되므로 경고로 남긴다 (URL 변경 감지용)
                if not feed.entries:
                    logger.warning(f"arXiv {category} 피드가 비어 있음: {feed_url}")
                    continue

                collected: list[Article] = []
                for entry in feed.entries:
                    announce_type = entry.get("arxiv_announce_type", "new")
                    if announce_type not in self.ANNOUNCE_TYPES:
                        continue

                    try:
                        article = self._parse_entry(entry, category)
                    except Exception as e:
                        logger.warning(f"arXiv 항목 파싱 실패: {e}")
                        continue

                    if article and article.id not in seen_ids:
                        seen_ids.add(article.id)
                        collected.append(article)

                if collected:
                    per_category.append(collected)
                logger.info(
                    f"arXiv {category}: {len(feed.entries)}건 중 {len(collected)}건 수집"
                )

            except Exception as e:
                logger.error(f"arXiv {category} 크롤링 실패: {e}")
                continue

        # arXiv RSS는 같은 발표 회차의 논문에 모두 동일한 published를 부여하므로
        # 시간 정렬이 무의미하다. 카테고리를 번갈아 뽑아 편중을 막는다.
        articles = self._interleave(per_category)[:max_items]

        logger.info(f"arXiv 총 {len(articles)}건 수집")
        return articles

    @staticmethod
    def _interleave(groups: list[list[Article]]) -> list[Article]:
        """카테고리별 목록을 라운드로빈으로 병합"""
        merged: list[Article] = []
        for items in zip_longest(*groups):
            merged.extend(item for item in items if item is not None)
        return merged

    def _parse_entry(self, entry: dict, category: str) -> Optional[Article]:
        """RSS 항목을 Article로 변환"""
        link = entry.get("link", "")
        arxiv_id = self._extract_arxiv_id(link)
        if not arxiv_id:
            return None

        title = entry.get("title", "").strip()
        # 줄바꿈 제거. 접두어 제거는 하지 않는다 —
        # 현재 arXiv RSS 제목에는 카테고리 접두어가 없어서
        # "TANGO: Humanoid Navigation..." 같은 제목의 앞부분이 잘려나간다.
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
        # arXiv RSS summary 접두어 제거
        # ("arXiv:2609.05437v1 Announce Type: new \nAbstract: 본문...")
        summary = re.sub(r"^arXiv:\S+\s*", "", summary).strip()
        summary = re.sub(r"^Announce Type:\s*\S+\s*", "", summary).strip()
        summary = re.sub(r"^Abstract:\s*", "", summary).strip()
        summary = re.sub(r"\s+", " ", summary).strip()

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
