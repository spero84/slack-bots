"""스토리지 테스트"""
import pytest
from datetime import datetime

from src.storage.models import Announcement, Source
from src.storage.s3_storage import deduplicate_announcements


class TestDeduplication:
    """중복 제거 테스트"""

    def test_deduplicate_same_title(self):
        """동일 제목 중복 제거"""
        kstartup = [
            Announcement(
                id="k1",
                source=Source.KSTARTUP,
                title="2024년 AI 창업 지원사업",
                url="https://kstartup.com/k1",
            ),
        ]
        bizinfo = [
            Announcement(
                id="b1",
                source=Source.BIZINFO,
                title="2024년 AI 창업 지원사업",  # 동일 제목
                url="https://bizinfo.com/b1",
            ),
        ]

        result = deduplicate_announcements(kstartup, bizinfo)

        # K-Startup 우선, 중복 제거
        assert len(result) == 1
        assert result[0].source == Source.KSTARTUP

    def test_deduplicate_different_titles(self):
        """다른 제목은 유지"""
        kstartup = [
            Announcement(
                id="k1",
                source=Source.KSTARTUP,
                title="AI 창업 지원",
                url="https://kstartup.com/k1",
            ),
        ]
        bizinfo = [
            Announcement(
                id="b1",
                source=Source.BIZINFO,
                title="IT 스타트업 육성",  # 다른 제목
                url="https://bizinfo.com/b1",
            ),
        ]

        result = deduplicate_announcements(kstartup, bizinfo)

        assert len(result) == 2

    def test_normalized_title_comparison(self):
        """정규화된 제목으로 비교"""
        kstartup = [
            Announcement(
                id="k1",
                source=Source.KSTARTUP,
                title="[2024] AI 창업 지원사업",
                url="https://kstartup.com/k1",
            ),
        ]
        bizinfo = [
            Announcement(
                id="b1",
                source=Source.BIZINFO,
                title="2024 AI창업지원사업",  # 공백/특수문자만 다름
                url="https://bizinfo.com/b1",
            ),
        ]

        result = deduplicate_announcements(kstartup, bizinfo)

        # 정규화 후 동일하므로 중복 제거
        assert len(result) == 1
