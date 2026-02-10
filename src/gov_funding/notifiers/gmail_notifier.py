"""Gmail API 기반 이메일 알림"""
import base64
import json
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..storage import Announcement, NotificationPayload
from ..utils import get_config, logger


class GmailNotifier:
    """Gmail API를 이용한 이메일 알림"""

    def __init__(
        self,
        credentials_json: Optional[str] = None,
        recipients: Optional[list[str]] = None,
    ):
        config = get_config()
        self.credentials_json = credentials_json or config.gmail_credentials
        self.recipients = recipients or config.email_recipients
        self._service = None

    def _get_credentials_from_secrets(self) -> Optional[dict]:
        """환경변수에서 Gmail 자격증명 조회"""
        if not self.credentials_json:
            logger.warning("GMAIL_CREDENTIALS 환경변수가 설정되지 않음")
            return None
        try:
            return json.loads(self.credentials_json)
        except json.JSONDecodeError:
            logger.error("Gmail 자격증명 JSON 파싱 실패")
            return None

    def _build_service(self):
        """Gmail API 서비스 빌드"""
        if self._service is not None:
            return self._service

        creds_data = self._get_credentials_from_secrets()
        if not creds_data:
            raise ValueError("Gmail 자격증명을 찾을 수 없습니다")

        credentials = Credentials(
            token=creds_data.get("access_token"),
            refresh_token=creds_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_data.get("client_id"),
            client_secret=creds_data.get("client_secret"),
        )

        self._service = build("gmail", "v1", credentials=credentials)
        return self._service

    async def send_notification(self, payload: NotificationPayload) -> bool:
        """이메일 알림 전송

        Args:
            payload: 알림 페이로드

        Returns:
            전송 성공 여부
        """
        if not payload.has_content:
            logger.info("알림 내용 없음 - 이메일 전송 스킵")
            return True

        if not self.recipients:
            logger.warning("이메일 수신자가 설정되지 않음")
            return False

        try:
            service = self._build_service()
            message = self._create_message(payload)

            for recipient in self.recipients:
                result = service.users().messages().send(
                    userId="me",
                    body={"raw": message},
                ).execute()
                logger.info(f"이메일 전송 완료: {recipient} (ID: {result.get('id')})")

            return True

        except Exception as e:
            logger.error(f"Gmail 전송 오류: {e}")
            return False

    def _create_message(self, payload: NotificationPayload) -> str:
        """이메일 메시지 생성"""
        today = datetime.now().strftime("%Y-%m-%d")
        subject = f"[지원사업 알림] 신규 공고 {len(payload.new_announcements)}건 ({today})"

        # HTML 본문 생성
        html_body = self._build_html_body(payload)

        # MIME 메시지 생성
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = "me"
        msg["To"] = ", ".join(self.recipients)

        # 텍스트 버전
        text_body = self._build_text_body(payload)
        msg.attach(MIMEText(text_body, "plain", "utf-8"))

        # HTML 버전
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        return base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def _build_html_body(self, payload: NotificationPayload) -> str:
        """HTML 이메일 본문 생성"""
        today = datetime.now().strftime("%Y년 %m월 %d일")

        html = f"""
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

        # 신규 공고
        if payload.new_announcements:
            html += f"""
        <div class="section">
            <div class="section-title">🆕 신규 공고 ({len(payload.new_announcements)}건)</div>
"""
            for ann in payload.new_announcements:
                html += self._announcement_html(ann)
            html += "</div>"

        # 마감 임박
        if payload.deadline_soon:
            html += f"""
        <div class="section">
            <div class="section-title">🔴 마감 임박 ({len(payload.deadline_soon)}건)</div>
"""
            for ann in payload.deadline_soon:
                html += self._announcement_html(ann, urgent=True)
            html += "</div>"

        html += """
        <div class="footer">
            <p>🤖 Gov Funding Monitor | K-Startup, 기업마당에서 자동 수집</p>
        </div>
    </div>
</body>
</html>
"""
        return html

    def _announcement_html(self, ann: Announcement, urgent: bool = False) -> str:
        """단일 공고 HTML 생성"""
        urgent_class = " deadline-urgent" if urgent else ""

        meta_parts = []
        if ann.category:
            meta_parts.append(f"분야: {ann.category}")
        if ann.d_day is not None:
            d_day_text = f"D-{ann.d_day}" if ann.d_day > 0 else "마감"
            meta_parts.append(f"마감: {d_day_text}")
        if ann.organization:
            meta_parts.append(f"기관: {ann.organization}")

        meta_text = " | ".join(meta_parts)

        summary_html = ""
        if ann.summary:
            summary_html = f'<div class="announcement-summary">{ann.summary}</div>'

        return f"""
            <div class="announcement{urgent_class}">
                <div class="announcement-title"><a href="{ann.url}">{ann.title}</a></div>
                <div class="announcement-meta">{meta_text}</div>
                {summary_html}
            </div>
"""

    def _build_text_body(self, payload: NotificationPayload) -> str:
        """텍스트 이메일 본문 생성"""
        today = datetime.now().strftime("%Y년 %m월 %d일")
        lines = [
            f"📢 지원사업 공고 알림 ({today})",
            "=" * 50,
            "",
        ]

        if payload.new_announcements:
            lines.append(f"🆕 신규 공고 ({len(payload.new_announcements)}건)")
            lines.append("-" * 30)
            for ann in payload.new_announcements:
                lines.append(f"• {ann.title}")
                lines.append(f"  {ann.url}")
                if ann.d_day is not None:
                    lines.append(f"  마감: D-{ann.d_day}")
                lines.append("")

        if payload.deadline_soon:
            lines.append(f"🔴 마감 임박 ({len(payload.deadline_soon)}건)")
            lines.append("-" * 30)
            for ann in payload.deadline_soon:
                lines.append(f"• {ann.title}")
                lines.append(f"  {ann.url}")
                lines.append(f"  마감: D-{ann.d_day}")
                lines.append("")

        lines.append("-" * 50)
        lines.append("🤖 Gov Funding Monitor | K-Startup, 기업마당에서 자동 수집")

        return "\n".join(lines)


async def send_gmail_notification(payload: NotificationPayload) -> bool:
    """편의 함수: Gmail 알림 전송"""
    notifier = GmailNotifier()
    return await notifier.send_notification(payload)
