"""모델 테스트"""
import pytest
from datetime import datetime, timedelta

from src.storage.models import Announcement, Source, NotificationPayload


class TestAnnouncement:
    """Announcement 모델 테스트"""

    def test_create_announcement(self):
        """공고 생성 테스트"""
        ann = Announcement(
            id="12345",
            source=Source.KSTARTUP,
            title="2024년 AI 스타트업 지원사업",
            category="IT/SW",
            d_day=10,
            url="https://example.com/12345",
        )

        assert ann.id == "12345"
        assert ann.source == Source.KSTARTUP
        assert ann.title == "2024년 AI 스타트업 지원사업"
        assert ann.d_day == 10
        assert not ann.is_deadline_soon

    def test_deadline_soon(self):
        """마감 임박 판단 테스트"""
        # D-5: 마감 임박
        ann1 = Announcement(
            id="1", source=Source.KSTARTUP, title="Test", url="https://test.com", d_day=5
        )
        assert ann1.is_deadline_soon

        # D-10: 마감 임박 아님
        ann2 = Announcement(
            id="2", source=Source.KSTARTUP, title="Test", url="https://test.com", d_day=10
        )
        assert not ann2.is_deadline_soon

        # D-day 없음
        ann3 = Announcement(
            id="3", source=Source.KSTARTUP, title="Test", url="https://test.com"
        )
        assert not ann3.is_deadline_soon

    def test_normalized_title(self):
        """제목 정규화 테스트"""
        ann = Announcement(
            id="1",
            source=Source.KSTARTUP,
            title="2024년 [AI] 스타트업 지원사업 (2차)",
            url="https://test.com",
        )
        # 특수문자, 공백 제거 후 소문자
        assert "2024년ai스타트업지원사업2차" == ann.normalized_title

    def test_announcement_equality(self):
        """공고 동등성 테스트"""
        ann1 = Announcement(
            id="12345", source=Source.KSTARTUP, title="Test", url="https://test.com"
        )
        ann2 = Announcement(
            id="12345", source=Source.KSTARTUP, title="Different", url="https://test.com"
        )
        ann3 = Announcement(
            id="12345", source=Source.BIZINFO, title="Test", url="https://test.com"
        )

        # 같은 ID + 같은 출처 = 동일
        assert ann1 == ann2
        # 같은 ID + 다른 출처 = 다름
        assert ann1 != ann3


class TestNotificationPayload:
    """NotificationPayload 테스트"""

    def test_empty_payload(self):
        """빈 페이로드 테스트"""
        payload = NotificationPayload()
        assert not payload.has_content
        assert payload.total_count == 0

    def test_payload_with_content(self):
        """내용 있는 페이로드 테스트"""
        ann = Announcement(
            id="1", source=Source.KSTARTUP, title="Test", url="https://test.com"
        )
        payload = NotificationPayload(new_announcements=[ann])

        assert payload.has_content
        assert payload.total_count == 1
