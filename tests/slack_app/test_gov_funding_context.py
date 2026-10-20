"""slack-app gov-funding Q&A 컨텍스트 테스트 (마감일 기준 D-day 계산)"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def slack_app(monkeypatch):
    # slack-app은 별도 venv라 다른 서비스 venv에서는 스킵된다
    pytest.importorskip("dotenv")
    pytest.importorskip("slack_bolt")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    from src.slack_app import app

    return app


def _deadline(days: int) -> str:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    return f"{(today + timedelta(days=days)).isoformat()}T23:59:59"


def _vector(title: str, days=None, stored_d_day: int = 3) -> dict:
    meta = {"title": title, "d_day": stored_d_day}
    if days is not None:
        meta["deadline"] = _deadline(days)
    return {"metadata": meta}


def test_deadline_question_does_not_filter_on_stored_d_day(slack_app):
    assert slack_app._extract_metadata_filter("마감 임박한 공고 알려줘") is None
    assert slack_app._wants_deadline_soon("마감 임박한 공고 알려줘")
    assert slack_app._wants_deadline_soon("이번 주 마감 공고")
    assert not slack_app._wants_deadline_soon("AI 바우처 마감일 언제야?")
    assert slack_app._extract_metadata_filter("기업마당 공고") == {"source": "bizinfo"}


def test_select_deadline_soon_uses_actual_deadline(slack_app):
    vectors = [
        _vector("이미 마감", days=-3, stored_d_day=2),
        _vector("D-20 유사도 1위", days=20, stored_d_day=4),
        _vector("마감일 없음"),
        _vector("D-5", days=5),
        _vector("오늘 마감", days=0),
        _vector("D-30", days=30),
    ]

    titles = [v["metadata"]["title"] for v in slack_app._select_deadline_soon(vectors)]

    # 유사도 상위 2건(마감 전) 유지 + 마감 임박 순
    assert titles == ["D-20 유사도 1위", "마감일 없음", "오늘 마감", "D-5", "D-30"]
    assert "이미 마감" not in titles


def test_select_deadline_soon_falls_back_to_open_items(slack_app):
    vectors = [_vector("이미 마감", days=-1), _vector("D-20", days=20), _vector("마감일 없음")]

    titles = [v["metadata"]["title"] for v in slack_app._select_deadline_soon(vectors)]

    assert titles == ["D-20", "마감일 없음"]


def test_format_shows_computed_d_day(slack_app):
    text = slack_app._format_vector_results([
        _vector("곧 마감", days=4, stored_d_day=30),
        _vector("지난 공고", days=-2, stored_d_day=5),
    ])

    assert "D-4" in text
    assert "D-30" not in text
    assert "마감됨" in text
    assert "D-5" not in text
