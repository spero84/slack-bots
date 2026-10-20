"""날짜 유틸리티 (KST 기준)

서버(EC2) 시간대는 UTC다. 공고 마감일은 한국 시간 기준이므로
오늘 날짜와 D-day는 반드시 KST로 계산한다.
"""
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """현재 시각 (KST, naive)

    마감일(deadline)은 naive datetime(KST 23:59:59)으로 저장되므로 비교용으로 naive를 반환한다.
    """
    return datetime.now(KST).replace(tzinfo=None)


def today_kst() -> date:
    """오늘 날짜 (KST)"""
    return now_kst().date()


def calculate_d_day(deadline: Optional[datetime], today: Optional[date] = None) -> Optional[int]:
    """D-day 계산

    Returns:
        마감일까지 남은 일수 (오늘 마감이면 0). 마감일이 없거나 지났으면 None.
    """
    if deadline is None:
        return None
    today = today or today_kst()
    delta = (deadline.date() - today).days
    return delta if delta >= 0 else None
