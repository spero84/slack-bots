"""필터링 테스트"""
import pytest

from src.analyzers.relevance_filter import (
    keyword_filter,
    calculate_keyword_score,
    _contains_relevance_keyword,
    _contains_exclude_keyword,
)
from src.storage.models import Announcement, Source


class TestKeywordFilter:
    """키워드 필터링 테스트"""

    def test_relevance_keyword_detection(self):
        """관련 키워드 감지 테스트"""
        assert _contains_relevance_keyword("AI 스타트업 지원")
        assert _contains_relevance_keyword("소프트웨어 개발")
        assert _contains_relevance_keyword("IT 창업")
        assert _contains_relevance_keyword("인공지능 R&D")
        assert not _contains_relevance_keyword("농업 지원사업")

    def test_exclude_keyword_detection(self):
        """제외 키워드 감지 테스트"""
        assert _contains_exclude_keyword("농업 혁신 지원")
        assert _contains_exclude_keyword("축산업 현대화")
        assert _contains_exclude_keyword("조선업 지원")
        assert not _contains_exclude_keyword("AI 스타트업")

    def test_keyword_filter(self):
        """키워드 필터링 테스트"""
        announcements = [
            Announcement(id="1", source=Source.KSTARTUP, title="AI 창업 지원사업", url="https://test.com"),
            Announcement(id="2", source=Source.KSTARTUP, title="농업 현대화 사업", url="https://test.com"),
            Announcement(id="3", source=Source.BIZINFO, title="IT 스타트업 R&D", url="https://test.com"),
            Announcement(id="4", source=Source.BIZINFO, title="건설업 활성화", url="https://test.com"),
        ]

        filtered = keyword_filter(announcements)

        # AI, IT 관련만 통과
        assert len(filtered) == 2
        assert filtered[0].id == "1"
        assert filtered[1].id == "3"

    def test_keyword_score(self):
        """키워드 점수 계산 테스트"""
        # 많은 키워드 = 높은 점수
        score1 = calculate_keyword_score("AI 스타트업 창업 IT SW 개발")
        # 적은 키워드 = 낮은 점수
        score2 = calculate_keyword_score("일반 지원사업")

        assert score1 > score2
        assert 0 <= score1 <= 1
        assert 0 <= score2 <= 1
