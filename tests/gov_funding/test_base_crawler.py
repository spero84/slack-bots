"""BaseCrawler.download_attachment() 테스트"""
import asyncio
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from src.gov_funding.crawlers.base_crawler import BaseCrawler
from src.gov_funding.storage import Announcement, Source


class DummyCrawler(BaseCrawler):
    """테스트용 크롤러 구현체"""

    source = Source.MSIT
    name = "테스트"

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        return []

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        return None


@pytest.fixture
def crawler():
    return DummyCrawler()


@pytest.mark.asyncio
async def test_download_attachment_success(crawler):
    """정상 다운로드 시 파일 바이트 반환"""
    mock_resp = MagicMock()
    mock_resp.content = b"fake-hwp-content"
    mock_resp.raise_for_status = MagicMock()

    with patch("src.gov_funding.crawlers.base_crawler.requests.get", return_value=mock_resp) as mock_get:
        result = await crawler.download_attachment("https://example.com/file.hwp")

    assert result == b"fake-hwp-content"
    mock_get.assert_called_once_with("https://example.com/file.hwp", timeout=30, allow_redirects=True)


@pytest.mark.asyncio
async def test_download_attachment_custom_timeout(crawler):
    """커스텀 타임아웃 전달"""
    mock_resp = MagicMock()
    mock_resp.content = b"data"
    mock_resp.raise_for_status = MagicMock()

    with patch("src.gov_funding.crawlers.base_crawler.requests.get", return_value=mock_resp) as mock_get:
        await crawler.download_attachment("https://example.com/file.hwp", timeout=60)

    mock_get.assert_called_once_with("https://example.com/file.hwp", timeout=60, allow_redirects=True)


@pytest.mark.asyncio
async def test_download_attachment_http_error(crawler):
    """HTTP 오류 시 None 반환"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("404 Not Found")

    with patch("src.gov_funding.crawlers.base_crawler.requests.get", return_value=mock_resp):
        result = await crawler.download_attachment("https://example.com/missing.hwp")

    assert result is None


@pytest.mark.asyncio
async def test_download_attachment_network_error(crawler):
    """네트워크 오류 시 None 반환"""
    with patch("src.gov_funding.crawlers.base_crawler.requests.get", side_effect=ConnectionError("timeout")):
        result = await crawler.download_attachment("https://example.com/file.hwp")

    assert result is None
