#!/usr/bin/env python3
"""Slack 알림 테스트 스크립트"""
import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# .env 파일 로드
load_dotenv(project_root / ".env")

from src.notifiers.slack_notifier import SlackNotifier
from src.storage.models import Announcement, NotificationPayload, Source


async def test_slack_notification():
    """Slack 알림 테스트"""
    print("🔔 Slack 알림 테스트 시작...")
    print(f"   채널 ID: {os.environ.get('SLACK_CHANNEL_ID')}")
    print(f"   Bot Token: {os.environ.get('SLACK_BOT_TOKEN', '')[:20]}...")

    # 테스트용 공고 데이터
    test_announcements = [
        Announcement(
            id="test001",
            source=Source.KSTARTUP,
            title="[테스트] 2026년 AI 스타트업 지원사업",
            category="IT/SW",
            d_day=15,
            organization="창업진흥원",
            url="https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?schM=view&pbancSn=test001",
            summary="AI/SW 스타트업 대상 최대 3억원 지원. 예비창업자 및 3년 이내 초기창업자 대상.",
        ),
        Announcement(
            id="test002",
            source=Source.BIZINFO,
            title="[테스트] 데이터 바우처 지원사업",
            category="기술",
            d_day=5,
            organization="한국데이터산업진흥원",
            url="https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/view.do?pblancId=test002",
            summary="데이터 구매·가공 바우처 최대 7천만원 지원.",
        ),
    ]

    # 마감 임박 테스트
    deadline_soon = [
        Announcement(
            id="test003",
            source=Source.KSTARTUP,
            title="[테스트] 창업도약패키지 지원사업",
            category="사업화",
            d_day=3,
            organization="창업진흥원",
            url="https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?schM=view&pbancSn=test003",
        ),
    ]

    payload = NotificationPayload(
        new_announcements=test_announcements,
        deadline_soon=deadline_soon,
    )

    print(f"\n📤 전송할 내용:")
    print(f"   - 신규 공고: {len(payload.new_announcements)}건")
    print(f"   - 마감 임박: {len(payload.deadline_soon)}건")

    # Slack 알림 전송
    notifier = SlackNotifier()
    success = await notifier.send_notification(payload)

    if success:
        print("\n✅ Slack 알림 전송 성공!")
    else:
        print("\n❌ Slack 알림 전송 실패")

    return success


if __name__ == "__main__":
    success = asyncio.run(test_slack_notification())
    sys.exit(0 if success else 1)
