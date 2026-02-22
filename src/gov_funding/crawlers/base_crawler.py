"""크롤러 기본 클래스"""
from abc import ABC, abstractmethod
from typing import Optional

import requests

from ..storage import Announcement, Source
from ..utils import logger


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

    async def download_attachment(self, url: str, timeout: int = 30) -> Optional[bytes]:
        """첨부파일 다운로드

        Args:
            url: 파일 다운로드 URL
            timeout: 타임아웃 (초)

        Returns:
            파일 바이트 또는 None (실패 시)
        """
        try:
            resp = requests.get(url, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.warning(f"첨부파일 다운로드 실패 ({url}): {e}")
            return None

    async def close(self):
        """리소스 정리"""
        pass
