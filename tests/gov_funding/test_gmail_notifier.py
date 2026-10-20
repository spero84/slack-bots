"""GmailNotifier 테스트 (오프라인 - Gmail API 서비스는 MagicMock, 실제 전송 없음)"""
import base64
import email
import logging
from datetime import date
from email.header import decode_header, make_header
from unittest.mock import MagicMock

import pytest

from src.gov_funding.notifiers import gmail_notifier as gn
from src.gov_funding.notifiers import slack_notifier as sn
from src.gov_funding.notifiers.gmail_notifier import GmailNotifier, send_gmail_notification
from src.gov_funding.storage import Announcement, NotificationPayload, Source

FROZEN_TODAY = date(2026, 10, 21)
LOGGER_NAME = "gov-funding-monitor"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """실제 Gmail API 빌드 차단 + KST 오늘 날짜 고정 + 환경변수 격리"""
    def _no_build(*args, **kwargs):
        raise AssertionError("테스트에서 실제 Gmail API를 빌드하면 안 됨")

    monkeypatch.setattr(gn, "build", _no_build)
    monkeypatch.setattr(gn, "today_kst", lambda: FROZEN_TODAY)
    monkeypatch.setattr(sn, "today_kst", lambda: FROZEN_TODAY)
    monkeypatch.delenv("GMAIL_CREDENTIALS", raising=False)
    monkeypatch.delenv("EMAIL_RECIPIENTS", raising=False)


@pytest.fixture
def logs(caplog):
    """'gov-funding-monitor' 로거 기록 수집

    로거가 propagate=False여도 기록되도록, 그 경우에만 caplog 핸들러를 직접 부착한다
    (propagate=True일 때 직접 부착하면 같은 기록이 두 번 잡힌다).
    """
    target = logging.getLogger(LOGGER_NAME)
    attach = not target.propagate
    if attach:
        target.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    yield caplog
    if attach:
        target.removeHandler(caplog.handler)


def make_ann(i, **kw):
    base = dict(
        id=str(i),
        source=Source.MSS,
        title=f"공고{i}",
        url=f"https://www.mss.go.kr/view?cbIdx=310&bcIdx={i}",
        d_day=10,
    )
    base.update(kw)
    return Announcement(**base)


def make_service(message_id="m1"):
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": message_id
    }
    return service


def send_mock(service):
    return service.users.return_value.messages.return_value.send


def decode_raw(raw: str) -> email.message.Message:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw))


def html_part(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8")
    raise AssertionError("HTML 파트 없음")


def text_part(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            return part.get_payload(decode=True).decode("utf-8")
    raise AssertionError("텍스트 파트 없음")


def subject_of(msg) -> str:
    return str(make_header(decode_header(msg["Subject"])))


PAYLOAD = NotificationPayload(new_announcements=[make_ann(1)])


# ---------------------------------------------------------------------------
# 미설정 → None, ERROR 없음
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("credentials,recipients", [
    ("", ["a@example.com"]),
    ("   ", ["a@example.com"]),
    ('{"refresh_token": "x"}', []),
    ("", []),
])
async def test_unconfigured_returns_none_without_error(logs, credentials, recipients):
    notifier = GmailNotifier(credentials_json=credentials, recipients=recipients)
    assert notifier.is_configured is False
    assert await notifier.send_notification(PAYLOAD) is None

    assert not [r for r in logs.records if r.levelno >= logging.WARNING]
    assert any(
        r.levelno == logging.INFO and r.getMessage() == "Gmail 미설정 - 이메일 전송 스킵"
        for r in logs.records
    )


async def test_unconfigured_from_env_returns_none(monkeypatch, logs):
    monkeypatch.setenv("GMAIL_CREDENTIALS", "")
    monkeypatch.setenv("EMAIL_RECIPIENTS", "a@example.com,b@example.com")
    assert await send_gmail_notification(PAYLOAD) is None
    assert not [r for r in logs.records if r.levelno >= logging.ERROR]


def test_env_config_is_used_when_args_omitted(monkeypatch):
    monkeypatch.setenv("GMAIL_CREDENTIALS", '{"refresh_token": "r"}')
    monkeypatch.setenv("EMAIL_RECIPIENTS", "a@example.com, b@example.com")
    notifier = GmailNotifier()
    assert notifier.is_configured is True
    assert notifier.recipients == ["a@example.com", "b@example.com"]


# ---------------------------------------------------------------------------
# 전송
# ---------------------------------------------------------------------------

async def test_single_send_for_all_recipients():
    notifier = GmailNotifier(credentials_json='{"refresh_token": "r"}', recipients=["a@example.com", "b@example.com"])
    service = make_service()
    notifier._service = service

    assert await notifier.send_notification(PAYLOAD) is True

    send = send_mock(service)
    assert send.call_count == 1
    kwargs = send.call_args.kwargs
    assert kwargs["userId"] == "me"
    msg = decode_raw(kwargs["body"]["raw"])
    assert msg["To"] == "a@example.com, b@example.com"


async def test_nothing_to_send_returns_true_without_api_call():
    notifier = GmailNotifier(credentials_json='{"refresh_token": "r"}', recipients=["a@example.com"])
    service = make_service()
    notifier._service = service
    assert await notifier.send_notification(NotificationPayload()) is True
    assert send_mock(service).call_count == 0


async def test_api_failure_returns_false_and_logs_error(logs):
    notifier = GmailNotifier(credentials_json='{"refresh_token": "r"}', recipients=["a@example.com"])
    service = make_service()
    send_mock(service).return_value.execute.side_effect = RuntimeError("quota exceeded")
    notifier._service = service

    assert await notifier.send_notification(PAYLOAD) is False
    errors = [r for r in logs.records if r.levelno == logging.ERROR]
    assert errors and "quota exceeded" in errors[0].getMessage()


async def test_invalid_credentials_json_is_error(logs):
    notifier = GmailNotifier(credentials_json="{not json", recipients=["a@example.com"])
    assert notifier.is_configured is True
    assert await notifier.send_notification(PAYLOAD) is False
    errors = [r.getMessage() for r in logs.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "JSON 파싱 실패" in errors[0]


# ---------------------------------------------------------------------------
# 본문 / 제목
# ---------------------------------------------------------------------------

def build_message(payload, recipients=("a@example.com",)):
    notifier = GmailNotifier(credentials_json='{"refresh_token": "r"}', recipients=list(recipients))
    return decode_raw(notifier._create_message(payload))


def test_html_escapes_untrusted_fields():
    ann = make_ann(
        1,
        title="AI <바우처> & R&D 공고",
        url='https://x.invalid/?a=1&b="2"',
        summary="요약입니다.</br>\n<script>alert(1)</script>둘째 줄",
        category="R&D <IT>",
        organization="기관 & <재단>",
        d_day=0,
    )
    html_body = html_part(build_message(NotificationPayload(new_announcements=[ann])))

    assert "AI &lt;바우처&gt; &amp; R&amp;D 공고" in html_body
    assert "<바우처>" not in html_body
    assert 'href="https://x.invalid/?a=1&amp;b=&quot;2&quot;"' in html_body
    assert "</br>" not in html_body
    assert "<script>" not in html_body
    assert "분야: R&amp;D &lt;IT&gt;" in html_body
    assert "기관: 기관 &amp; &lt;재단&gt;" in html_body
    assert "마감: D-Day(오늘 마감)" in html_body
    assert "요약입니다.<br>" in html_body


def test_non_http_url_is_not_linked():
    ann = make_ann(1, url="javascript:alert(1)")
    html_body = html_part(build_message(NotificationPayload(new_announcements=[ann])))
    assert "javascript:" not in html_body


def test_subject_from_counts():
    payload = NotificationPayload(
        new_announcements=[make_ann(1)],
        deadline_soon=[make_ann(2, d_day=3), make_ann(3, d_day=1)],
    )
    assert subject_of(build_message(payload)) == "[지원사업 알림] 신규 1건, 마감임박 2건 (2026-10-21)"

    deadline_only = NotificationPayload(deadline_soon=[make_ann(2, d_day=3)])
    assert subject_of(build_message(deadline_only)).startswith("[지원사업 알림] 신규 0건, 마감임박 1건")


def test_footer_lists_present_sources():
    payload = NotificationPayload(
        new_announcements=[
            make_ann(1, source=Source.BIZINFO),
            make_ann(2, source=Source.BIZINFO),
            make_ann(3, source=Source.KSTARTUP),
        ],
        deadline_soon=[make_ann(4, source=Source.NIPA, d_day=3)],
    )
    msg = build_message(payload)
    footer = "🤖 Gov Funding Monitor | 기업마당, K-Startup, NIPA에서 자동 수집"
    assert footer in html_part(msg)
    assert footer in text_part(msg)
    assert "K-Startup, 기업마당에서 자동 수집" not in html_part(msg)


def test_text_body_deadline_labels():
    payload = NotificationPayload(
        new_announcements=[make_ann(1, d_day=None), make_ann(2, d_day=0)],
        deadline_soon=[make_ann(3, d_day=3)],
    )
    text = text_part(build_message(payload))
    assert "D-None" not in text
    assert "마감: 미상" in text
    assert "마감: D-Day(오늘 마감)" in text
    assert "마감: D-3" in text


def test_header_date_uses_kst():
    html_body = html_part(build_message(PAYLOAD))
    assert "2026년 10월 21일" in html_body
