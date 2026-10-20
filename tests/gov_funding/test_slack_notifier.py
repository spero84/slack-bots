"""SlackNotifier 테스트 (오프라인 - requests.post는 항상 가짜로 대체)"""
import re
from datetime import date, datetime

import pytest
import requests

from src.gov_funding.notifiers import slack_notifier as sn
from src.gov_funding.notifiers.slack_notifier import (
    MAX_BLOCKS_PER_MESSAGE,
    MAX_MESSAGE_CHARS,
    SlackNotifier,
    escape_mrkdwn,
    format_d_day,
    format_summary,
    send_slack_notification,
    slack_link,
)
from src.gov_funding.storage import Announcement, NotificationPayload, Source

FROZEN_TODAY = date(2026, 10, 21)


def make_ann(i, title=None, summary=None, d_day=10, source=Source.MSS, url=None, **kw):
    return Announcement(
        id=str(i),
        source=source,
        title=title if title is not None else f"공고{i:03d}",
        d_day=d_day,
        url=url or f"https://www.mss.go.kr/site/smba/ex/bbs/View.do?cbIdx=310&bcIdx={i}",
        summary=summary,
        **kw,
    )


class FakeResponse:
    def __init__(self, data=None, status_code=200, headers=None):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if self._data is None:
            raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
        return self._data


class FakeSlackAPI:
    """requests.post 대체: 호출을 기록하고 큐의 응답을 순서대로 반환 (큐가 비면 ok)"""

    def __init__(self):
        self.calls: list[dict] = []
        self.responses: list = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return FakeResponse({"ok": True, "ts": "1700000000.000100"})

    @property
    def bodies(self) -> list[dict]:
        return [c["json"] for c in self.calls]


@pytest.fixture(autouse=True)
def slack_api(monkeypatch):
    """실제 Slack API 호출 차단 + KST 오늘 날짜 고정"""
    api = FakeSlackAPI()
    monkeypatch.setattr(sn.requests, "post", api)
    monkeypatch.setattr(sn, "today_kst", lambda: FROZEN_TODAY)
    return api


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def notifier(sleeps):
    return SlackNotifier(bot_token="xoxb-test", channel_id="CTEST", sleep_func=sleeps.append)


def section_texts(blocks):
    return [b["text"]["text"] for b in blocks if b["type"] == "section"]


def all_texts(blocks):
    texts = []
    for b in blocks:
        if b["type"] in ("section", "header"):
            texts.append(b["text"]["text"])
        elif b["type"] == "context":
            texts.extend(e["text"] for e in b["elements"])
    return texts


def count_in_bodies(bodies, needle):
    """needle이 등장하는 메시지 수"""
    return sum(1 for body in bodies if any(needle in t for t in all_texts(body["blocks"])))


# ---------------------------------------------------------------------------
# 이스케이프 / 포맷 헬퍼
# ---------------------------------------------------------------------------

def test_escape_mrkdwn():
    assert escape_mrkdwn("A <B> & C") == "A &lt;B&gt; &amp; C"
    assert escape_mrkdwn("<!channel>") == "&lt;!channel&gt;"
    assert escape_mrkdwn(None) == ""


def test_slack_link_encodes_url_and_escapes_label():
    link = slack_link("https://x.go.kr/v?a=1&b=2|3<4>5 6", "A <B> & C | D")
    assert link == "<https://x.go.kr/v?a=1&amp;b=2%7C3%3C4%3E5%206|A &lt;B&gt; &amp; C ｜ D>"
    inner = re.findall(r"<([^<>]*)>", link)
    assert len(inner) == 1 and inner[0].count("|") == 1


def test_slack_link_without_url_returns_label():
    assert slack_link("", "제목 <1>") == "제목 &lt;1&gt;"


def test_format_summary_cleans_html_newlines_and_underscores():
    assert format_summary("x</br>\ny_z") == "_x yz_"
    assert format_summary("운영된다.&lt;/br&gt;") == "_운영된다._"
    assert format_summary("  ") == ""
    assert format_summary(None) == ""


def test_format_summary_truncates():
    result = format_summary("가" * 2000)
    assert result.startswith("_") and result.endswith("…_")
    assert len(result) <= 500 + 2


@pytest.mark.parametrize("d_day,expected", [
    (None, None),
    (0, "D-Day(오늘 마감)"),
    (1, "D-1"),
    (7, "D-7"),
    (-1, "마감"),
])
def test_format_d_day(d_day, expected):
    assert format_d_day(d_day) == expected


# ---------------------------------------------------------------------------
# 공고 블록
# ---------------------------------------------------------------------------

def test_announcement_block_escapes_untrusted_text(notifier):
    ann = make_ann(
        1,
        title="A <B> & C | D",
        summary="x</br>\ny_z",
        category="R&D <IT>",
        organization="기관 & <재단>",
        url="https://x.go.kr/v?a=1&b=2",
    )
    text = notifier._announcement_block(ann)["text"]["text"]

    assert "&lt;B&gt;" in text
    assert "&amp; C" in text
    assert "<B>" not in text
    assert "분야: R&amp;D &lt;IT&gt;" in text
    assert "기관: 기관 &amp; &lt;재단&gt;" in text
    assert "</br>" not in text

    links = re.findall(r"<([^<>]*)>", text)
    assert links == ["https://x.go.kr/v?a=1&amp;b=2|A &lt;B&gt; &amp; C ｜ D"]
    assert all(link.count("|") == 1 for link in links)

    summary_line = text.split("\n")[-1]
    assert re.fullmatch(r"_[^\n_]*_", summary_line)
    assert summary_line == "_x yz_"


def test_announcement_block_unescapes_entities_in_title(notifier):
    text = notifier._announcement_block(make_ann(1, title="공고 &lt;변경&gt; R&amp;D"))["text"]["text"]
    # 엔티티를 한 번 풀고 다시 이스케이프 → Slack에서 '공고 <변경> R&D'로 보인다
    assert "공고 &lt;변경&gt; R&amp;D" in text
    assert "&amp;lt;" not in text


def test_announcement_block_truncates_title(notifier):
    text = notifier._announcement_block(make_ann(1, title="가" * 1000))["text"]["text"]
    label = re.findall(r"<[^<>|]*\|([^<>]*)>", text)[0]
    assert len(label) <= 300
    assert label.endswith("…")


def test_announcement_block_section_text_guard(notifier):
    ann = make_ann(
        1,
        title="&" * 1000,
        summary="&" * 3000,
        category="&" * 500,
        organization="<" * 500,
        url="https://x.go.kr/?q=" + "a" * 1500,
    )
    text = notifier._announcement_block(ann)["text"]["text"]
    assert len(text) <= 3000


@pytest.mark.parametrize("d_day,highlight,expected,unexpected", [
    (0, False, "마감: D-Day(오늘 마감)", "🚨"),
    (0, True, "마감: *D-Day(오늘 마감)* 🚨", None),
    (3, True, "마감: *D-3* 🚨", None),
    (5, True, "마감: D-5", "🚨"),
    (5, False, "마감: D-5", "🚨"),
])
def test_d_day_rendering(notifier, d_day, highlight, expected, unexpected):
    text = notifier._announcement_block(make_ann(1, d_day=d_day), highlight_deadline=highlight)["text"]["text"]
    assert expected in text
    if unexpected:
        assert unexpected not in text


def test_d_day_unknown_renders_misang(notifier):
    text = notifier._announcement_block(make_ann(1, d_day=None), highlight_deadline=True)["text"]["text"]
    assert "마감: 미상" in text
    assert "🚨" not in text


def test_d_day_none_with_past_deadline_renders_closed(notifier):
    ann = make_ann(1, d_day=None, deadline=datetime(2026, 10, 20, 23, 59, 59))
    text = notifier._announcement_block(ann)["text"]["text"]
    assert "마감: 마감" in text
    assert "미상" not in text


def test_d_day_none_with_future_deadline_uses_kst_today(notifier):
    ann = make_ann(1, d_day=None, deadline=datetime(2026, 10, 23, 23, 59, 59))
    text = notifier._announcement_block(ann, highlight_deadline=True)["text"]["text"]
    assert "마감: *D-2* 🚨" in text


# ---------------------------------------------------------------------------
# 전송 / 메시지 구성
# ---------------------------------------------------------------------------

async def test_unfurl_disabled_on_every_post(notifier, slack_api):
    payload = NotificationPayload(
        new_announcements=[make_ann(i) for i in range(15)],
        deadline_soon=[make_ann(100, d_day=3)],
    )
    assert await notifier.send_notification(payload) is True
    assert len(slack_api.calls) == 2
    for body in slack_api.bodies:
        assert body["unfurl_links"] is False
        assert body["unfurl_media"] is False
        assert body["channel"] == "CTEST"


async def test_single_page_keeps_deadline_in_first_message(notifier, slack_api):
    payload = NotificationPayload(
        new_announcements=[make_ann(i) for i in range(3)],
        deadline_soon=[make_ann(100, title="마감임박X", d_day=2)],
    )
    assert await notifier.send_notification(payload) is True
    assert len(slack_api.calls) == 1
    body = slack_api.bodies[0]
    assert "thread_ts" not in body
    assert count_in_bodies(slack_api.bodies, "마감임박X") == 1
    assert body["text"] == "지원사업 공고 알림 (4건)"


async def test_multipage_deadline_posted_once_in_first_message(notifier, slack_api):
    payload = NotificationPayload(
        new_announcements=[make_ann(i) for i in range(15)],
        deadline_soon=[make_ann(900, title="마감임박X", d_day=3)],
    )
    assert await notifier.send_notification(payload) is True

    bodies = slack_api.bodies
    assert [b["text"] for b in bodies] == [
        "지원사업 공고 알림 (16건)",
        "지원사업 공고 알림 (계속 2/2)",
    ]
    rendered = [
        i for i, b in enumerate(bodies) for t in section_texts(b["blocks"]) if "마감임박X" in t
    ]
    assert rendered == [0]  # 채널(최상위) 메시지에 정확히 한 번
    assert "*🔴 마감 임박 (1건)*" in section_texts(bodies[0]["blocks"])
    assert count_in_bodies(bodies[1:], "마감 임박") == 0
    assert "thread_ts" not in bodies[0]
    assert all(b["thread_ts"] == "1700000000.000100" for b in bodies[1:])
    # 첫 메시지 섹션 순서: 신규 → 마감 임박
    titles = [t for t in section_texts(bodies[0]["blocks"]) if t.startswith("*")]
    assert titles == ["*🆕 신규 공고 (15건)*", "*🔴 마감 임박 (1건)*"]


def assert_each_once(bodies, anns):
    for ann in anns:
        label = f"|{ann.title}>"
        hits = sum(1 for b in bodies for t in section_texts(b["blocks"]) if label in t)
        assert hits == 1, ann.title


def assert_within_limits(bodies):
    for body in bodies:
        assert len(body["blocks"]) <= MAX_BLOCKS_PER_MESSAGE
        assert sum(len(t) for t in all_texts(body["blocks"])) <= MAX_MESSAGE_CHARS
        for text in section_texts(body["blocks"]):
            assert len(text) <= 3000


async def test_block_and_char_limits_10_new_60_deadline(notifier, slack_api):
    new = [make_ann(i, title=f"신규{i:03d}", summary="요약 " * 300) for i in range(10)]
    deadline = [
        make_ann(100 + i, title=f"임박{i:03d}", d_day=i % 8, summary="요약 " * 300) for i in range(60)
    ]
    payload = NotificationPayload(new_announcements=new, deadline_soon=deadline)

    assert await notifier.send_notification(payload) is True
    bodies = slack_api.bodies
    assert len(bodies) >= 2
    assert_within_limits(bodies)
    assert_each_once(bodies, new + deadline)

    # 신규 10건은 모두 첫 메시지, 마감 임박도 첫 메시지에서 시작하고 나머지는 바로 다음 스레드부터
    first_items = [t for t in section_texts(bodies[0]["blocks"]) if t.startswith("• ")]
    assert sum("|신규" in t for t in first_items) == 10
    assert sum("|임박" in t for t in first_items) >= 1
    assert "|임박000>" in "".join(first_items)
    assert any(t.startswith("*🔴 마감 임박 (60건 중 1~") for t in section_texts(bodies[0]["blocks"]))
    assert all(count_in_bodies([b], "신규") == 0 for b in bodies[1:])
    assert bodies[1]["text"].startswith("마감 임박 공고 (계속 ")


async def test_first_message_fills_up_to_50_blocks_then_overflows(notifier, slack_api):
    new = [make_ann(i, title=f"신규{i:03d}") for i in range(10)]
    deadline = [make_ann(100 + i, title=f"임박{i:03d}", d_day=3) for i in range(60)]
    payload = NotificationPayload(new_announcements=new, deadline_soon=deadline)

    assert await notifier.send_notification(payload) is True
    bodies = slack_api.bodies
    # 헤더2 + 신규(제목+10+구분선) + 마감(제목+구분선) + 푸터 = 17 → 마감 33건이 첫 메시지에 들어감
    assert len(bodies[0]["blocks"]) == MAX_BLOCKS_PER_MESSAGE
    assert "*🔴 마감 임박 (60건 중 1~33건, 나머지는 스레드)*" in section_texts(bodies[0]["blocks"])
    assert [b["text"] for b in bodies[1:]] == ["마감 임박 공고 (계속 34~60/60건)"]
    assert "*🔴 마감 임박 (계속 34~60/60건)*" in section_texts(bodies[1]["blocks"])
    assert bodies[1]["thread_ts"] == "1700000000.000100"
    assert_within_limits(bodies)
    assert_each_once(bodies, new + deadline)


async def test_overflow_comes_before_remaining_new_pages(notifier, slack_api):
    new = [make_ann(i, title=f"신규{i:03d}") for i in range(25)]
    deadline = [make_ann(100 + i, title=f"임박{i:03d}", d_day=5) for i in range(40)]
    payload = NotificationPayload(new_announcements=new, deadline_soon=deadline)

    assert await notifier.send_notification(payload) is True
    assert [b["text"] for b in slack_api.bodies] == [
        "지원사업 공고 알림 (65건)",
        "마감 임박 공고 (계속 34~40/40건)",
        "지원사업 공고 알림 (계속 2/3)",
        "지원사업 공고 알림 (계속 3/3)",
    ]
    assert_within_limits(slack_api.bodies)
    assert_each_once(slack_api.bodies, new + deadline)


async def test_deadline_only(notifier, slack_api):
    payload = NotificationPayload(deadline_soon=[make_ann(i, d_day=3) for i in range(60)])
    assert await notifier.send_notification(payload) is True
    bodies = slack_api.bodies
    assert bodies[0]["blocks"][0]["text"]["text"] == "⏰ 마감 리마인더 (2026-10-21)"
    assert [b["text"] for b in bodies] == [
        "지원사업 공고 알림 (60건)",
        "마감 임박 공고 (계속 46~60/60건)",
    ]
    assert_within_limits(bodies)
    assert_each_once(bodies, payload.deadline_soon)


def test_fit_count_respects_char_budget(notifier):
    item = notifier._announcement_block(make_ann(1, summary="가" * 500))
    size = len(item["text"]["text"])
    count = notifier._fit_count([item] * 49, used=[])
    assert count == (MAX_MESSAGE_CHARS - 100) // size
    assert count < 48


def test_split_oversized_guard():
    blocks = [{"type": "divider"}] * 120
    messages = SlackNotifier._split_oversized([(blocks, "t")])
    assert [len(b) for b, _ in messages] == [50, 50, 20]
    assert [t for _, t in messages] == ["t", "t (2/3)", "t (3/3)"]


async def test_header_uses_kst_today(notifier, slack_api):
    await notifier.send_notification(NotificationPayload(new_announcements=[make_ann(1)]))
    header = slack_api.bodies[0]["blocks"][0]
    assert header["type"] == "header"
    assert header["text"]["text"] == "📢 지원사업 공고 알림 (2026-10-21)"


async def test_footer_lists_only_present_sources(notifier, slack_api):
    payload = NotificationPayload(
        new_announcements=[
            make_ann(1, source=Source.BIZINFO),
            make_ann(2, source=Source.BIZINFO),
            make_ann(3, source=Source.KSTARTUP),
        ],
    )
    await notifier.send_notification(payload)
    footer = slack_api.bodies[0]["blocks"][-1]
    assert footer["type"] == "context"
    assert footer["elements"][0]["text"] == "🤖 Gov Funding Monitor | 기업마당, K-Startup에서 수집"


def test_footer_many_sources_is_short(notifier):
    anns = [make_ann(i, source=s) for i, s in enumerate(
        [Source.KSTARTUP, Source.BIZINFO, Source.NIPA, Source.NIA, Source.MSS]
    )]
    text = notifier._footer_block(anns)["elements"][0]["text"]
    assert text == "🤖 Gov Funding Monitor | K-Startup, 기업마당, NIPA 등 5곳에서 수집"


async def test_ops_notes_appended_to_first_message_only(notifier, slack_api):
    payload = NotificationPayload(new_announcements=[make_ann(i) for i in range(12)])
    notes = ["jointips: 0건", "Bedrock 분석 실패 5/5건", "  "]
    assert await notifier.send_notification(payload, ops_notes=notes) is True

    bodies = slack_api.bodies
    assert len(bodies) == 2
    ops_blocks = [
        b for b in bodies[0]["blocks"]
        if b["type"] == "context" and b["elements"][0]["text"].startswith("⚠️ 수집 이상:")
    ]
    assert len(ops_blocks) == 1
    assert ops_blocks[0]["elements"][0]["text"] == "⚠️ 수집 이상: jointips: 0건 · Bedrock 분석 실패 5/5건"
    assert count_in_bodies(bodies[1:], "수집 이상") == 0


async def test_ops_notes_are_escaped(notifier, slack_api):
    payload = NotificationPayload(new_announcements=[make_ann(1)])
    await notifier.send_notification(payload, ops_notes=["<!channel> & 오류"])
    texts = all_texts(slack_api.bodies[0]["blocks"])
    assert "⚠️ 수집 이상: &lt;!channel&gt; &amp; 오류" in texts


async def test_ops_only_message(notifier, slack_api):
    notes = ["jointips: 0건", "Bedrock 분석 실패 5/5건"]
    assert await notifier.send_notification(NotificationPayload(), ops_notes=notes) is True
    assert len(slack_api.calls) == 1
    blocks = slack_api.bodies[0]["blocks"]
    assert blocks[0] == {
        "type": "header",
        "text": {"type": "plain_text", "text": "⚠️ Gov Funding Monitor 수집 이상 (2026-10-21)", "emoji": True},
    }
    assert section_texts(blocks) == ["• jointips: 0건\n• Bedrock 분석 실패 5/5건"]
    assert len(blocks) == 2
    assert slack_api.bodies[0]["unfurl_links"] is False


async def test_empty_payload_without_notes_sends_nothing(notifier, slack_api):
    assert await notifier.send_notification(NotificationPayload()) is True
    assert await notifier.send_notification(NotificationPayload(), ops_notes=[]) is True
    assert slack_api.calls == []


async def test_missing_config_returns_false(monkeypatch, slack_api):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_CHANNEL_ID", raising=False)
    notifier = SlackNotifier()
    assert await notifier.send_notification(NotificationPayload(new_announcements=[make_ann(1)])) is False
    assert slack_api.calls == []


# ---------------------------------------------------------------------------
# 재시도 / 실패
# ---------------------------------------------------------------------------

async def test_ratelimit_retry_then_success(notifier, slack_api, sleeps):
    slack_api.responses = [
        FakeResponse({"ok": True, "ts": "1.1"}),
        FakeResponse({"ok": False, "error": "ratelimited"}, headers={"Retry-After": "2"}),
        FakeResponse({"ok": True, "ts": "1.2"}),
        FakeResponse(None, status_code=429),  # JSON 아닌 429 + Retry-After 없음 → 기본 1초
        FakeResponse({"ok": True, "ts": "1.3"}),
    ]
    payload = NotificationPayload(new_announcements=[make_ann(i) for i in range(25)])

    assert await notifier.send_notification(payload) is True
    assert len(slack_api.calls) == 5
    assert sleeps == [2.0, 1.0]
    texts = [b["text"] for b in slack_api.bodies]
    assert texts == [
        "지원사업 공고 알림 (25건)",
        "지원사업 공고 알림 (계속 2/3)",
        "지원사업 공고 알림 (계속 2/3)",
        "지원사업 공고 알림 (계속 3/3)",
        "지원사업 공고 알림 (계속 3/3)",
    ]
    assert all(b["thread_ts"] == "1.1" for b in slack_api.bodies[1:])


async def test_ratelimit_gives_up_after_three_attempts(notifier, slack_api, sleeps):
    slack_api.responses = [
        FakeResponse({"ok": False, "error": "ratelimited"}, status_code=429, headers={"Retry-After": "5"})
        for _ in range(5)
    ]
    payload = NotificationPayload(new_announcements=[make_ann(1)])
    assert await notifier.send_notification(payload) is False
    assert len(slack_api.calls) == 3
    assert sleeps == [5.0, 5.0]


def test_retry_after_is_capped():
    assert sn._retry_after_seconds(FakeResponse(headers={"Retry-After": "9999"})) == sn.MAX_RETRY_AFTER
    assert sn._retry_after_seconds(FakeResponse(headers={"Retry-After": "abc"})) == 1.0
    assert sn._retry_after_seconds(FakeResponse()) == 1.0


async def test_retry_uses_time_sleep_by_default(monkeypatch, slack_api):
    slept = []
    monkeypatch.setattr(sn.time, "sleep", slept.append)
    slack_api.responses = [
        FakeResponse(None, status_code=429, headers={"Retry-After": "3"}),
        FakeResponse({"ok": True, "ts": "1"}),
    ]
    notifier = SlackNotifier(bot_token="xoxb-test", channel_id="CTEST")
    assert await notifier.send_notification(NotificationPayload(new_announcements=[make_ann(1)])) is True
    assert slept == [3.0]


async def test_permanent_failure_mid_pagination_returns_false(notifier, slack_api, sleeps):
    slack_api.responses = [
        FakeResponse({"ok": True, "ts": "1.1"}),
        FakeResponse({"ok": False, "error": "invalid_blocks"}),
    ]
    payload = NotificationPayload(new_announcements=[make_ann(i) for i in range(25)])
    assert await notifier.send_notification(payload) is False
    assert len(slack_api.calls) == 2  # 영구 오류는 재시도하지 않고 중단
    assert sleeps == []


async def test_deadline_overflow_failure_returns_false(notifier, slack_api):
    slack_api.responses = [
        FakeResponse({"ok": True, "ts": "1.1"}),
        FakeResponse({"ok": False, "error": "channel_not_found"}),
    ]
    payload = NotificationPayload(
        new_announcements=[make_ann(i) for i in range(15)],
        deadline_soon=[make_ann(100 + i, d_day=3) for i in range(40)],
    )
    assert await notifier.send_notification(payload) is False
    assert len(slack_api.calls) == 2
    assert slack_api.bodies[1]["text"].startswith("마감 임박 공고 (계속 ")


@pytest.mark.parametrize("failure", [
    requests.exceptions.ConnectionError("down"),
    requests.exceptions.Timeout("slow"),
    FakeResponse(None, status_code=502),
])
async def test_network_or_non_json_failure_returns_false(notifier, slack_api, failure):
    slack_api.responses = [failure]
    payload = NotificationPayload(new_announcements=[make_ann(1)])
    assert await notifier.send_notification(payload) is False
    assert len(slack_api.calls) == 1


async def test_send_slack_notification_passes_ops_notes(monkeypatch, slack_api):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "CENV")
    assert await send_slack_notification(NotificationPayload(), ops_notes=["jointips: 0건"]) is True
    assert len(slack_api.calls) == 1
    assert slack_api.calls[0]["headers"]["Authorization"] == "Bearer xoxb-env"
    assert slack_api.bodies[0]["channel"] == "CENV"
    assert await send_slack_notification(NotificationPayload()) is True
    assert len(slack_api.calls) == 1
