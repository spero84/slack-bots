"""aec-news Slack 메시지 구성 테스트 (국가별 섹션, 같은 소식 묶기, 이스케이프, 블록 한도)"""
from datetime import datetime
from unittest.mock import patch

import requests

from src.aec_news.config import COMPETITORS, KST
from src.aec_news.models import Region, Theme
from src.aec_news.slack_notifier import (
    MAX_BLOCKS,
    DigestContext,
    SlackMessage,
    SlackNotifier,
    build_messages,
    escape_mrkdwn,
    group_stories,
    slack_link,
)

from .conftest import make_item

CTX = DigestContext(
    run_at=datetime(2026, 11, 13, 8, 30, tzinfo=KST),
    days_by_region={Region.KR: 2, Region.US: 7, Region.SG: 7},
)


def all_text(messages) -> str:
    parts = []
    for message in messages:
        parts.append(message.text)
        for b in message.blocks:
            if "text" in b and isinstance(b["text"], dict):
                parts.append(b["text"]["text"])
            for e in b.get("elements", []):
                parts.append(e.get("text", ""))
    return "\n".join(parts)


def test_escape_and_link():
    assert escape_mrkdwn("A&B <x>") == "A&amp;B &lt;x&gt;"
    link = slack_link("https://ex.com/a?b=1|2", "제목 | 부제 <특집>")
    assert link == "<https://ex.com/a?b=1%7C2|제목 ¦ 부제 &lt;특집&gt;>"


def test_empty_digest_still_reports_run():
    messages = build_messages([], [], CTX)
    assert len(messages) == 1
    text = all_text(messages)
    assert "새 소식 없음" in text
    assert "11/13(금)" in text
    assert "국내 최근 2일 · 해외 최근 7일" in text


def test_regions_and_summaries_rendered():
    items = [
        make_item(1, title="쪼개 읽던 산업 도면, AI가 통째로 읽는다", score=0.95, theme=Theme.DRAWING_BIM,
                  summary="인포시즈·서울대가 도면 특화 AI 개발에 나섬", url="https://www.donga.com/news/1", story=1),
        make_item(2, title="AI Reads Blueprints", outlet_key="reuters", outlet_name="Reuters",
                  region=Region.US, score=0.8, theme=Theme.AEC_AI, url="https://www.reuters.com/x", story=2),
    ]
    text = all_text(build_messages(items, [], CTX))
    assert "🇰🇷 국내" in text and "🇺🇸 미국" in text
    assert "<https://www.donga.com/news/1|쪼개 읽던 산업 도면, AI가 통째로 읽는다>" in text
    assert "_인포시즈·서울대가 도면 특화 AI 개발에 나섬_" in text
    assert "AI × 도면/BIM" in text
    assert "새 소식 2건" in text
    assert text.index("🇰🇷 국내") < text.index("🇺🇸 미국")
    assert "🇸🇬 싱가포르: 새 기사 없음" in text


def test_hours_override_period_text():
    ctx = DigestContext(run_at=CTX.run_at, days_by_region=CTX.days_by_region, hours=12)
    assert "최근 12시간" in all_text(build_messages([], [], ctx))


def test_same_story_grouped_under_lead():
    a = make_item(1, title="인포시즈, 서울대와 도면 AI", outlet_key="hankyung", outlet_name="한국경제",
                  score=0.9, story=1, url="https://hankyung.com/1")
    b = make_item(2, title="쪼개 읽던 산업 도면, AI가 통째로 읽는다", score=0.95, story=1,
                  url="https://donga.com/2")
    c = make_item(3, title="별개 기사", score=0.7, story=3)
    groups = group_stories([a, b, c])
    assert len(groups) == 2
    assert groups[0].lead is b and groups[0].others == [a]
    text = all_text(build_messages([a, b, c], [], CTX))
    assert "같은 소식: <https://hankyung.com/1|한국경제>" in text
    assert "새 소식 2건* (기사 3개)" in text


def test_infra_section_and_warnings():
    infra = [make_item(9, title="건설사, AI 데이터센터 수주", score=0.4)]
    ctx = DigestContext(run_at=CTX.run_at, days_by_region=CTX.days_by_region,
                        warnings=["Google News 검색 2/15회 실패"])
    text = all_text(build_messages([], infra, ctx))
    assert "📎 참고" in text
    assert "건설사, AI 데이터센터 수주" in text
    assert "⚠️ Google News 검색 2/15회 실패" in text


def test_block_limit_splits_into_thread():
    items = [make_item(i, score=0.8, story=i) for i in range(1, 80)]
    messages = build_messages(items, [], CTX)
    assert len(messages) >= 2
    assert all(len(m.blocks) <= 50 for m in messages)
    assert len(messages[0].blocks) <= MAX_BLOCKS
    # 모든 기사가 어딘가에 들어가고, 메시지별 기사 목록이 실제 블록 내용과 맞는다
    text = all_text(messages)
    assert all(f"기사 제목 {i}" in text for i in range(1, 80))
    assert sorted(i.google_id for m in messages for i in m.items) == sorted(i.google_id for i in items)
    for m in messages:
        body = all_text([m])
        assert all(i.title in body for i in m.items)
    assert all("_label" not in b for m in messages for b in m.blocks)


def test_split_never_ends_message_on_region_header():
    # 첫 메시지 예산(45블록)이 국내 끝 직후 미국 제목에서 끝나도록 맞춘다
    kr = [make_item(i, score=0.9, story=i) for i in range(1, 42)]       # divider + 제목 + 41 = 43블록
    us = [make_item(100 + i, score=0.9, story=100 + i, region=Region.US, outlet_key="reuters",
                    outlet_name="Reuters") for i in range(5)]
    messages = build_messages(kr + us, [], CTX)
    assert len(messages) >= 2
    first_body = [b for b in messages[0].blocks if b["type"] != "context"]
    last = first_body[-1]
    assert not (last["type"] == "section" and last["text"]["text"].startswith("*🇺🇸")), "미국 제목이 혼자 남음"
    assert last["type"] != "divider"
    assert "*🇺🇸 미국*" in all_text(messages[1:])


def test_long_story_trims_same_story_links_not_mid_link():
    lead = make_item(1, score=0.9, story=1, summary="요약")
    others = [make_item(i, score=0.5, story=1, outlet_name=f"매체{i}",
                        url=f"https://news.google.com/rss/articles/{'X' * 180}{i}?oc=5") for i in range(2, 40)]
    messages = build_messages([lead, *others], [], CTX)
    story = next(b for b in messages[0].blocks if b["type"] == "section" and "같은 소식" in b["text"]["text"])
    text = story["text"]["text"]
    assert len(text) <= 3000
    assert text.count("<") == text.count(">")    # 링크가 중간에서 잘리지 않음
    assert "외 " in text and "곳" in text


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def msg(text="a"):
    return SlackMessage([{"type": "divider"}], text)


def test_send_posts_thread_replies_with_parent_ts():
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return FakeResponse({"ok": True, "ts": "111.222"})

    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="C0CGZRXP5JS")
    with patch("src.aec_news.slack_notifier.requests.post", side_effect=fake_post):
        delivered = notifier.send([msg("a"), msg("b")])
    assert delivered == [0, 1]
    assert calls[0]["channel"] == "C0CGZRXP5JS" and "thread_ts" not in calls[0]
    assert calls[1]["thread_ts"] == "111.222"
    assert calls[0]["unfurl_links"] is False


def test_send_reports_api_error():
    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="C1")
    with patch("src.aec_news.slack_notifier.requests.post",
               return_value=FakeResponse({"ok": False, "error": "not_in_channel"})):
        assert notifier.send([msg()]) == []


def test_failed_thread_reply_is_not_reported_delivered():
    responses = [FakeResponse({"ok": True, "ts": "1.2"}), FakeResponse({"ok": False, "error": "invalid_blocks"}),
                 FakeResponse({"ok": True, "ts": "1.3"})]
    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="C1")
    with patch("src.aec_news.slack_notifier.requests.post", side_effect=responses):
        assert notifier.send([msg("a"), msg("b"), msg("c")]) == [0, 2]


def test_send_retries_rate_limit_with_retry_after():
    sleeps = []
    responses = [FakeResponse({}, status=429, headers={"Retry-After": "7"}), FakeResponse({"ok": True, "ts": "1"})]
    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="C1", sleep=sleeps.append)
    with patch("src.aec_news.slack_notifier.requests.post", side_effect=responses):
        assert notifier.send([msg()]) == [0]
    assert sleeps == [7.0]


def test_send_gives_up_after_repeated_rate_limit():
    sleeps = []
    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="C1", sleep=sleeps.append)
    with patch("src.aec_news.slack_notifier.requests.post",
               return_value=FakeResponse({}, status=429, headers={"Retry-After": "120"})):
        assert notifier.send([msg()]) == []
    assert sleeps == [30, 30]     # Retry-After는 30초로 제한


def test_send_without_token_fails_fast():
    notifier = SlackNotifier(bot_token="", channel_id="")
    notifier.bot_token, notifier.channel_id = "", ""
    assert notifier.send([SlackMessage([], "a")]) == []


def test_competitor_section_comes_first_with_company_and_mention():
    comp = make_item(1, title="인포시즈, 서울대와 산업 도면 AI", score=0.9, story=1, summary="요약",
                     url="https://www.aitimes.com/1")
    comp.competitor, comp.mention = "infocz", "title"
    body_only = make_item(2, title="아이디어정보기술 파트너스 데이", score=0.5, story=2, url="https://dnews.co.kr/2")
    body_only.competitor, body_only.mention = "fobecon", "body"
    glob = make_item(3, title="Trunk Tools raises $40M", score=0.95, story=3, region=Region.GLOBAL,
                     outlet_key="web:www.businesswire.com", outlet_name="Business Wire")
    glob.competitor = "trunktools"
    main = make_item(4, title="주제 기사", score=0.8, story=4)
    messages = build_messages([main], [], CTX, competitors=[comp, body_only, glob])
    text = all_text(messages)
    assert "🏁 경쟁사 소식 3건" in text
    assert text.index("*🏁 경쟁사 동향*") < text.index("*🇰🇷 국내*")
    assert text.index("건설 스타트업 국내") < text.index("건설 스타트업 글로벌")
    assert "🏷 인포시즈 · 동아일보" in text
    assert "🏷 포비콘 · 동아일보 · 11/12(목) · 본문 언급" in text
    assert "🏷 Trunk Tools · Business Wire" in text
    assert f"경쟁사 {len(COMPETITORS)}곳" in text
    assert sorted(i.google_id for m_ in messages for i in m_.items) == sorted(
        i.google_id for i in [comp, body_only, glob, main])


def test_same_story_links_capped():
    lead = make_item(1, score=0.9, story=1)
    others = [make_item(i, score=0.5, story=1, outlet_name=f"매체{i}") for i in range(2, 12)]
    text = all_text(build_messages([lead, *others], [], CTX))
    assert "외 5곳" in text and "매체7" not in text


def test_repeats_note_in_footer():
    ctx = DigestContext(run_at=CTX.run_at, days_by_region=CTX.days_by_region, repeats_skipped=4)
    assert "다른 매체가 쓴 기사 4건은 뺐습니다" in all_text(build_messages([], [], ctx))


def test_upload_file_uses_external_upload_flow():
    calls = []

    def fake_post(url, headers=None, data=None, files=None, json=None, timeout=None):
        calls.append((url, data, files))
        if url.endswith("files.getUploadURLExternal"):
            return FakeResponse({"ok": True, "upload_url": "https://files.slack.com/upload/v1/abc", "file_id": "F123"})
        if url.startswith("https://files.slack.com/"):
            return FakeResponse({}, status=200)
        return FakeResponse({"ok": True})

    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="C0CGZRXP5JS")
    with patch("src.aec_news.slack_notifier.requests.post", side_effect=fake_post):
        ok = notifier.upload_file(b"<html></html>", "aec-news-20261114.html", "EPC 데일리", thread_ts="1.2",
                                  comment="📄 HTML")
    assert ok
    assert calls[0][1] == {"filename": "aec-news-20261114.html", "length": "13"}
    assert calls[1][0] == "https://files.slack.com/upload/v1/abc" and calls[1][2]["file"][0] == "aec-news-20261114.html"
    done = calls[2][1]
    assert done["channel_id"] == "C0CGZRXP5JS" and done["thread_ts"] == "1.2" and '"id": "F123"' in done["files"]


def test_upload_file_failure_returns_false():
    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="C1")
    with patch("src.aec_news.slack_notifier.requests.post",
               return_value=FakeResponse({"ok": False, "error": "missing_scope"})):
        assert notifier.upload_file(b"x", "a.html", "t") is False
