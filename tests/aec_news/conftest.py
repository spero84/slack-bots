"""aec-news 테스트 공통 픽스처"""
from datetime import datetime, timezone

import pytest

from src.aec_news.models import NewsItem, Region, Theme


def make_item(no: int = 1, *, title: str | None = None, outlet_key: str = "donga", outlet_name: str = "동아일보",
              region: Region = Region.KR, hour: int = 1, themes=(Theme.DRAWING_BIM,), url: str | None = None,
              snippet: str = "", score=None, theme=None, story=None, summary=None) -> NewsItem:
    return NewsItem(
        title=title or f"기사 제목 {no}",
        outlet_key=outlet_key,
        outlet_name=outlet_name,
        region=region,
        published_at=datetime(2026, 11, 12, hour, 0, tzinfo=timezone.utc),
        google_id=f"CBMi{no:04d}",
        google_url=f"https://news.google.com/rss/articles/CBMi{no:04d}?oc=5",
        host="www.donga.com",
        url=url,
        snippet=snippet,
        query_themes=list(themes),
        score=score,
        theme=theme,
        story=story,
        summary_ko=summary,
    )


@pytest.fixture
def item_factory():
    return make_item
