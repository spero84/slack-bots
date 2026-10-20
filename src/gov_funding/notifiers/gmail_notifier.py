"""Gmail API 기반 이메일 알림

GMAIL_CREDENTIALS 또는 EMAIL_RECIPIENTS가 비어 있으면 선택 채널이 꺼진 것으로 보고
INFO 로그만 남긴 채 None을 반환한다 (실제 전송 실패만 ERROR).
"""
import base64
import html
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..storage import Announcement, NotificationPayload
from ..utils import get_config, logger
from ..utils.dates import today_kst
from ..utils.text import clean_text
from .slack_notifier import (
    FOOTER_PREFIX,
    SOURCE_DISPLAY_NAMES,
    deadline_label,
    normalize_plain,
    summarize_sources,
)


def _esc(text: Optional[str]) -> str:
    """HTML 본문/속성용 이스케이프"""
    return html.escape(text or "", quote=True)


class GmailNotifier:
    """Gmail API를 이용한 이메일 알림"""

    def __init__(
        self,
        credentials_json: Optional[str] = None,
        recipients: Optional[list[str]] = None,
    ):
        config = get_config()
        # None일 때만 환경변수 값을 사용 (빈 문자열/빈 리스트는 명시적인 '미설정')
        self.credentials_json = (
            config.gmail_credentials if credentials_json is None else credentials_json
        )
        self.recipients = list(config.email_recipients if recipients is None else recipients)
        self._service = None

    @property
    def is_configured(self) -> bool:
        """자격증명과 수신자가 모두 설정되어 있는지"""
        return bool((self.credentials_json or "").strip() and self.recipients)

    def _get_credentials_from_secrets(self) -> dict:
        """환경변수의 Gmail 자격증명 JSON 파싱

        Raises:
            ValueError: 자격증명이 없거나 JSON 형식이 아닐 때
        """
        if not (self.credentials_json or "").strip():
            raise ValueError("Gmail 자격증명을 찾을 수 없습니다")
        try:
            data = json.loads(self.credentials_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Gmail 자격증명 JSON 파싱 실패: {e.msg}") from None
        if not isinstance(data, dict):
            raise ValueError("Gmail 자격증명 JSON 형식 오류 (객체가 아님)")
        return data

    def _build_service(self):
        """Gmail API 서비스 빌드"""
        if self._service is not None:
            return self._service

        creds_data = self._get_credentials_from_secrets()
        credentials = Credentials(
            token=creds_data.get("access_token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
        )

        self._service = build("gmail", "v1", credentials=credentials)
        return self._service

    async def send_notification(self, payload: NotificationPayload) -> Optional[bool]:
        """이메일 알림 전송 (수신자 전원을 To에 넣어 1회 전송)

        Args:
            payload: 알림 페이로드

        Returns:
            None: Gmail 미설정으로 스킵, True: 전송 성공(또는 보낼 내용 없음), False: 전송 실패
        """
        if not self.is_configured:
            logger.info("Gmail 미설정 - 이메일 전송 스킵")
            logger.debug(
                f"Gmail 설정 상태: 자격증명 {'있음' if (self.credentials_json or '').strip() else '없음'}, "
                f"수신자 {len(self.recipients)}명"
            )
            return None

        if not payload.new_announcements and not payload.deadline_soon:
            logger.info("알림 내용 없음 - 이메일 전송 스킵")
            return True

        try:
            service = self._build_service()
            message = self._create_message(payload)
            # Gmail은 To 헤더의 모든 주소로 배달하므로 한 번만 보낸다
            result = service.users().messages().send(
                userId="me",
                body={"raw": message},
            ).execute()
            logger.info(
                f"이메일 전송 완료: 수신자 {len(self.recipients)}명 (ID: {result.get('id')})"
            )
            return True

        except Exception as e:
            logger.error(f"Gmail 전송 오류: {e}")
            return False

    def _all_items(self, payload: NotificationPayload) -> list[Announcement]:
        return list(payload.new_announcements) + list(payload.deadline_soon)

    def _footer_text(self, payload: NotificationPayload) -> str:
        sources = summarize_sources(self._all_items(payload), max_names=len(SOURCE_DISPLAY_NAMES))
        return f"{FOOTER_PREFIX} | {sources}에서 자동 수집" if sources else FOOTER_PREFIX

    def _subject(self, payload: NotificationPayload) -> str:
        today = today_kst().strftime("%Y-%m-%d")
        return (
            f"[지원사업 알림] 신규 {len(payload.new_announcements)}건, "
            f"마감임박 {len(payload.deadline_soon)}건 ({today})"
        )

    def _create_message(self, payload: NotificationPayload) -> str:
        """이메일 메시지 생성 (base64url 인코딩된 raw MIME)"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = self._subject(payload)
        msg["From"] = "me"
        msg["To"] = ", ".join(self.recipients)

        msg.attach(MIMEText(self._build_text_body(payload), "plain", "utf-8"))
        msg.attach(MIMEText(self._build_html_body(payload), "html", "utf-8"))

        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def _build_html_body(self, payload: NotificationPayload) -> str:
        """HTML 이메일 본문 생성"""
        today = today_kst().strftime("%Y년 %m월 %d일")

        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #2563eb; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .header h1 {{ margin: 0; font-size: 20px; }}
        .section {{ background: #f8fafc; padding: 15px; margin-bottom: 15px; border-radius: 8px; }}
        .section-title {{ font-size: 16px; font-weight: bold; margin-bottom: 10px; }}
        .announcement {{ background: white; padding: 12px; margin-bottom: 8px; border-radius: 4px; border-left: 3px solid #2563eb; }}
        .announcement-title {{ font-weight: bold; margin-bottom: 5px; }}
        .announcement-title a {{ color: #2563eb; text-decoration: none; }}
        .announcement-meta {{ font-size: 12px; color: #64748b; }}
        .announcement-summary {{ font-size: 13px; color: #475569; margin-top: 5px; font-style: italic; }}
        .deadline-urgent {{ border-left-color: #dc2626; }}
        .footer {{ text-align: center; font-size: 12px; color: #94a3b8; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📢 지원사업 공고 알림</h1>
            <p style="margin: 5px 0 0; opacity: 0.9;">{today}</p>
        </div>
"""

        if payload.new_announcements:
            html_body += f"""
        <div class="section">
            <div class="section-title">🆕 신규 공고 ({len(payload.new_announcements)}건)</div>
"""
            for ann in payload.new_announcements:
                html_body += self._announcement_html(ann)
            html_body += "</div>"

        if payload.deadline_soon:
            html_body += f"""
        <div class="section">
            <div class="section-title">🔴 마감 임박 ({len(payload.deadline_soon)}건)</div>
"""
            for ann in payload.deadline_soon:
                html_body += self._announcement_html(ann, urgent=True)
            html_body += "</div>"

        html_body += f"""
        <div class="footer">
            <p>{_esc(self._footer_text(payload))}</p>
        </div>
    </div>
</body>
</html>
"""
        return html_body

    @staticmethod
    def _meta_text(ann: Announcement) -> str:
        """메타 정보 한 줄 (이스케이프 전 일반 텍스트)"""
        source = getattr(ann.source, "value", str(ann.source))
        meta_parts = [f"출처: {SOURCE_DISPLAY_NAMES.get(source, source)}"]
        category = normalize_plain(ann.category)
        if category:
            meta_parts.append(f"분야: {category}")
        meta_parts.append(f"마감: {deadline_label(ann)}")
        organization = normalize_plain(ann.organization)
        if organization:
            meta_parts.append(f"기관: {organization}")
        return " | ".join(meta_parts)

    def _announcement_html(self, ann: Announcement, urgent: bool = False) -> str:
        """단일 공고 HTML 생성 (제목/URL/메타/요약 모두 이스케이프)"""
        urgent_class = " deadline-urgent" if urgent else ""
        title = _esc(normalize_plain(ann.title) or "(제목 없음)")

        url = (ann.url or "").strip()
        if url.lower().startswith(("http://", "https://")):
            title_html = f'<a href="{_esc(url)}">{title}</a>'
        else:
            title_html = title

        summary_html = ""
        summary = clean_text(ann.summary)
        if summary:
            summary_html = (
                f'<div class="announcement-summary">{_esc(summary).replace(chr(10), "<br>")}</div>'
            )

        return f"""
            <div class="announcement{urgent_class}">
                <div class="announcement-title">{title_html}</div>
                <div class="announcement-meta">{_esc(self._meta_text(ann))}</div>
                {summary_html}
            </div>
"""

    def _build_text_body(self, payload: NotificationPayload) -> str:
        """텍스트 이메일 본문 생성"""
        today = today_kst().strftime("%Y년 %m월 %d일")
        lines = [
            f"📢 지원사업 공고 알림 ({today})",
            "=" * 50,
            "",
        ]

        sections = [
            ("🆕 신규 공고", payload.new_announcements),
            ("🔴 마감 임박", payload.deadline_soon),
        ]
        for title, items in sections:
            if not items:
                continue
            lines.append(f"{title} ({len(items)}건)")
            lines.append("-" * 30)
            for ann in items:
                lines.append(f"• {normalize_plain(ann.title)}")
                lines.append(f"  {ann.url}")
                lines.append(f"  마감: {deadline_label(ann)}")
                lines.append("")

        lines.append("-" * 50)
        lines.append(self._footer_text(payload))

        return "\n".join(lines)


async def send_gmail_notification(payload: NotificationPayload) -> Optional[bool]:
    """편의 함수: Gmail 알림 전송

    Returns:
        None: 미설정으로 스킵, True: 성공(또는 보낼 내용 없음), False: 실패
    """
    notifier = GmailNotifier()
    return await notifier.send_notification(payload)
