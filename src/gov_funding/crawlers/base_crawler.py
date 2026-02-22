"""크롤러 기본 클래스"""
from abc import ABC, abstractmethod
from typing import Optional

from ..storage import Announcement, Source


class BaseCrawler(ABC):
    """크롤러 추상 기본 클래스"""

    source: Source
    name: str

    @abstractmethod
    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 크롤링

        Args:
            max_items: 최대 수집 개수

        Returns:
            공고 목록
        """
        pass

    @abstractmethod
    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회

        Args:
            announcement_id: 공고 ID

        Returns:
            상세 정보 딕셔너리
        """
        pass

    async def close(self):
        """리소스 정리"""
        pass
