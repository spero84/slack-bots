"""크롤러 기본 클래스"""
from abc import ABC, abstractmethod

from ..storage.models import Article, ArticleSource


class BaseCrawler(ABC):
    """크롤러 추상 기본 클래스"""

    source: ArticleSource
    name: str

    @abstractmethod
    async def crawl(self, max_items: int = 20) -> list[Article]:
        """기사/논문 크롤링

        Args:
            max_items: 최대 수집 개수

        Returns:
            기사 목록
        """
        pass

    async def close(self):
        """리소스 정리"""
        pass
