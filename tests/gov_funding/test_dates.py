"""KST 날짜 유틸리티 테스트"""
from datetime import date, datetime, timezone
from unittest.mock import patch

from src.gov_funding.utils import dates
from src.gov_funding.utils.dates import calculate_d_day, now_kst, today_kst


class FrozenDatetime(datetime):
    """UTC 2026-10-20 15:30 (= KST 2026-10-21 00:30)"""

    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 10, 20, 15, 30, tzinfo=timezone.utc)
        return base.astimezone(tz) if tz else base.replace(tzinfo=None)


def test_today_kst_differs_from_utc_date_after_15_utc():
    with patch.object(dates, "datetime", FrozenDatetime):
        assert today_kst() == date(2026, 10, 21)
        assert now_kst() == datetime(2026, 10, 21, 0, 30)
        assert now_kst().tzinfo is None


def test_calculate_d_day():
    today = date(2026, 10, 20)
    assert calculate_d_day(datetime(2026, 10, 27, 23, 59, 59), today) == 7
    assert calculate_d_day(datetime(2026, 10, 20, 23, 59, 59), today) == 0
    assert calculate_d_day(datetime(2026, 10, 19, 23, 59, 59), today) is None
    assert calculate_d_day(None, today) is None
