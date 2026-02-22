"""_fetch_detail_content() 및 filter_with_bedrock() 통합 테스트"""
import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gov_funding.analyzers.bedrock_analyzer import (
    _fetch_detail_content,
    filter_with_bedrock,
)
from src.gov_funding.crawlers.base_crawler import BaseCrawler
from src.gov_funding.storage import Announcement, Source


class FakeCrawler(BaseCrawler):
    """테스트용 크롤러"""

    source = Source.MSS
    name = "테스트"

    def __init__(self, detail_result=None, download_result=None):
        self._detail_result = detail_result
        self._download_result = download_result

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        return []

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        return self._detail_result

    async def download_attachment(self, url: str, timeout: int = 30) -> Optional[bytes]:
        return self._download_result


def _make_announcement(source=Source.MSS, ann_id="test-1", title="테스트 공고") -> Announcement:
    return Announcement(
        id=ann_id,
        source=source,
        title=title,
        url="https://example.com/1",
    )


# --- _fetch_detail_content 테스트 ---


@pytest.mark.asyncio
async def test_fetch_detail_content_with_content_only():
    """상세 페이지 본문만 있는 경우"""
    crawler = FakeCrawler(
        detail_result={"content": "공고 상세 내용입니다.", "attachments": []}
    )
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result == "공고 상세 내용입니다."


@pytest.mark.asyncio
async def test_fetch_detail_content_with_hwp_attachment():
    """HWP 첨부파일이 있는 경우 텍스트 추출"""
    crawler = FakeCrawler(
        detail_result={
            "content": "페이지 본문",
            "attachments": [
                {"name": "공고문.hwp", "url": "https://example.com/file.hwp"},
            ],
        },
        download_result=b"fake-hwp-bytes",
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="HWP에서 추출한 텍스트",
    ) as mock_extract:
        result = await _fetch_detail_content(crawler, ann)

    mock_extract.assert_called_once_with(b"fake-hwp-bytes", "공고문.hwp")
    assert "페이지 본문" in result
    assert "HWP에서 추출한 텍스트" in result


@pytest.mark.asyncio
async def test_fetch_detail_content_with_hwpx_attachment():
    """HWPX 첨부파일도 처리"""
    crawler = FakeCrawler(
        detail_result={
            "content": "",
            "attachments": [
                {"name": "문서.hwpx", "url": "https://example.com/file.hwpx"},
            ],
        },
        download_result=b"fake-hwpx-bytes",
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="HWPX 텍스트",
    ):
        result = await _fetch_detail_content(crawler, ann)

    assert result == "HWPX 텍스트"


@pytest.mark.asyncio
async def test_fetch_detail_content_skips_non_hwp():
    """HWP/HWPX가 아닌 첨부파일은 무시"""
    crawler = FakeCrawler(
        detail_result={
            "content": "본문",
            "attachments": [
                {"name": "문서.pdf", "url": "https://example.com/file.pdf"},
                {"name": "이미지.png", "url": "https://example.com/img.png"},
            ],
        },
    )
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result == "본문"


@pytest.mark.asyncio
async def test_fetch_detail_content_only_first_hwp():
    """HWP 파일이 여러 개일 때 첫 번째만 처리"""
    call_count = 0

    class CountingCrawler(FakeCrawler):
        async def download_attachment(self, url, timeout=30):
            nonlocal call_count
            call_count += 1
            return b"bytes"

    crawler = CountingCrawler(
        detail_result={
            "content": "본문",
            "attachments": [
                {"name": "첫번째.hwp", "url": "https://example.com/1.hwp"},
                {"name": "두번째.hwp", "url": "https://example.com/2.hwp"},
            ],
        },
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="텍스트",
    ):
        await _fetch_detail_content(crawler, ann)

    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_detail_content_download_failure():
    """다운로드 실패 시 본문만 반환"""
    crawler = FakeCrawler(
        detail_result={
            "content": "본문만 사용",
            "attachments": [
                {"name": "공고문.hwp", "url": "https://example.com/file.hwp"},
            ],
        },
        download_result=None,  # 다운로드 실패
    )
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result == "본문만 사용"


@pytest.mark.asyncio
async def test_fetch_detail_content_get_detail_returns_none():
    """get_detail()이 None을 반환하는 경우"""
    crawler = FakeCrawler(detail_result=None)
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_detail_content_exception_graceful():
    """예외 발생 시 None 반환 (graceful fallback)"""

    class ErrorCrawler(FakeCrawler):
        async def get_detail(self, announcement_id):
            raise RuntimeError("연결 오류")

    crawler = ErrorCrawler()
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result is None


# --- filter_with_bedrock 통합 테스트 ---


@pytest.mark.asyncio
async def test_filter_with_bedrock_passes_detail_content():
    """crawlers 제공 시 detail_content가 analyze_relevance에 전달됨"""
    ann = _make_announcement(source=Source.MSS)

    crawler = FakeCrawler(
        detail_result={"content": "상세 내용", "attachments": []},
    )
    crawlers = {"mss": crawler}

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.BedrockAnalyzer"
    ) as MockAnalyzer:
        mock_instance = MockAnalyzer.return_value
        mock_instance.analyze_relevance = AsyncMock(return_value=(0.9, "요약"))

        result = await filter_with_bedrock([ann], threshold=0.7, crawlers=crawlers)

    # detail_content가 전달되었는지 확인
    mock_instance.analyze_relevance.assert_called_once()
    call_args = mock_instance.analyze_relevance.call_args
    assert call_args[0][0] == ann
    assert call_args[0][1] == "상세 내용"  # detail_content


@pytest.mark.asyncio
async def test_filter_with_bedrock_no_crawlers():
    """crawlers 미제공 시 기존 동작 (detail_content=None)"""
    ann = _make_announcement(source=Source.BIZINFO)

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.BedrockAnalyzer"
    ) as MockAnalyzer:
        mock_instance = MockAnalyzer.return_value
        mock_instance.analyze_relevance = AsyncMock(return_value=(0.8, "요약"))

        result = await filter_with_bedrock([ann], threshold=0.7)

    call_args = mock_instance.analyze_relevance.call_args
    assert call_args[0][1] is None  # detail_content가 None


@pytest.mark.asyncio
async def test_filter_with_bedrock_source_not_in_crawlers():
    """소스가 crawlers에 없으면 detail_content=None"""
    ann = _make_announcement(source=Source.BIZINFO)

    crawlers = {"mss": FakeCrawler()}

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.BedrockAnalyzer"
    ) as MockAnalyzer:
        mock_instance = MockAnalyzer.return_value
        mock_instance.analyze_relevance = AsyncMock(return_value=(0.8, "요약"))

        result = await filter_with_bedrock([ann], threshold=0.7, crawlers=crawlers)

    call_args = mock_instance.analyze_relevance.call_args
    assert call_args[0][1] is None
