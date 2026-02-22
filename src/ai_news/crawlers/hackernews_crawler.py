"""Hacker News Firebase API 크롤러"""
import re
from datetime import datetime
from typing import Optional

import requests

from ..storage.models import Article, ArticleCategory, ArticleSource
from ..utils.config import AI_KEYWORDS, CRAWL_SOURCES
from ..utils.logger import logger
from .base_crawler import BaseCrawler


class HackerNewsCrawler(BaseCrawler):
    """Hacker News Firebase API 크롤러 (AI 키워드 필터링)"""

    source = ArticleSource.HACKERNEWS
    name = "Hacker News"

    def __init__(self):
        self.config = CRAWL_SOURCES["hackernews"]
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "AI-News-Bot/1.0"})
        # 키워드 패턴 (대소문자 무시)
        self._keyword_pattern = re.compile(
            "|".join(re.escape(kw) for kw in AI_KEYWORDS),
            re.IGNORECASE,
        )

    async def crawl(self, max_items: int = 20) -> list[Article]:
        """Hacker News에서 AI 관련 기사 수집"""
        articles: list[Article] = []
        seen_ids: set[str] = set()

        # top + best 스토리에서 수집
        for url_key in ["top_url", "best_url"]:
            try:
                resp = self.session.get(self.config[url_key], timeout=10)
                resp.raise_for_status()
                story_ids = resp.json()[:50]  # 상위 50개만

                for story_id in story_ids:
                    sid = str(story_id)
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)

                    article = self._fetch_story(story_id)
                    if article:
                        articles.append(article)

                    if len(articles) >= max_items:
                        break

            except Exception as e:
                logger.error(f"HN {url_key} 크롤링 실패: {e}")
                continue

            if len(articles) >= max_items:
                break

        # score 기준 정렬
        articles.sort(
            key=lambda a: (a.extra or {}).get("score", 0),
            reverse=True,
        )
        articles = articles[:max_items]

        logger.info(f"Hacker News 총 {len(articles)}건 수집 (AI 필터링 후)")
        return articles

    def _fetch_story(self, story_id: int) -> Optional[Article]:
        """개별 스토리 조회 + AI 키워드 필터링"""
        try:
            url = self.config["item_url"].format(id=story_id)
            resp = self.session.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()

            if not data or data.get("type") != "story":
                return None

            title = data.get("title", "")
            story_url = data.get("url", "")
            text = data.get("text", "")

            # AI 키워드 필터링
            search_text = f"{title} {story_url} {text}"
            if not self._keyword_pattern.search(search_text):
                return None

            # score 하한선
            score = data.get("score", 0)
            if score < 10:
                return None

            published_at = None
            if data.get("time"):
                published_at = datetime.fromtimestamp(data["time"])

            # URL이 없으면 HN 토론 링크
            if not story_url:
                story_url = f"https://news.ycombinator.com/item?id={story_id}"

            return Article(
                id=str(story_id),
                source=ArticleSource.HACKERNEWS,
                category=ArticleCategory.INDUSTRY_NEWS,
                title=title,
                url=story_url,
                published_at=published_at,
                summary=text[:500] if text else None,
                extra={
                    "score": score,
                    "comments": data.get("descendants", 0),
                    "author": data.get("by", ""),
                    "hn_url": f"https://news.ycombinator.com/item?id={story_id}",
                },
            )

        except Exception as e:
            logger.debug(f"HN story {story_id} 조회 실패: {e}")
            return None

    async def close(self):
        self.session.close()
