"""Slack 알림 전송"""
import math
from datetime import datetime
from typing import Optional

import requests

from ..storage import Announcement, NotificationPayload
from ..utils import get_config, logger

PAGE_SIZE = 10

SOURCE_DISPLAY_NAMES = {
    "kstartup": "K-Startup",
    "bizinfo": "기업마당",
    "nipa": "NIPA",
    "nia": "NIA",
    "iitp": "IITP",
}


class SlackNotifier:
    """Slack Bot Token 기반 알림 전송"""

    def __init__(self, bot_token: Optional[str] = None, channel_id: Optional[str] = None):
        config = get_config()
        self.bot_token = bot_token or config.slack_bot_token
        self.channel_id = channel_id or config.slack_channel_id
        self.api_url = "https://slack.com/api/chat.postMessage"

    def _post_message(
        self,
        blocks: list[dict],
        text: str,
        thread_ts: Optional[str] = None,
    ) -> dict:
        """Slack chat.postMessage 호출"""
        body: dict = {
            "channel": self.channel_id,
            "blocks": blocks,
            "text": text,
        }
        if thread_ts:
            body["thread_ts"] = thread_ts

        response = requests.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30,
        )
        return response.json()

    async def send_notification(self, payload: NotificationPayload) -> bool:
        """알림 전송 (10개씩 페이지네이션, 스레드 reply)

        Args:
            payload: 알림 페이로드

        Returns:
            전송 성공 여부
        """
        if not payload.has_content:
            logger.info("알림 내용 없음 - 전송 스킵")
            return True

        if not self.bot_token or not self.channel_id:
            logger.error("Slack 설정 누락 (SLACK_BOT_TOKEN, SLACK_CHANNEL_ID)")
            return False

        total_new = len(payload.new_announcements)
        total_pages = max(math.ceil(total_new / PAGE_SIZE), 1)

        try:
            # 첫 메시지: 헤더 + 1~10번 공고 (+ 마감 임박 if 1 page only)
            include_deadline = (total_pages == 1)
            first_blocks = self._build_blocks_page(payload, page=0, include_deadline=include_deadline)
            result = self._post_message(
                first_blocks,
                text=f"지원사업 공고 알림 ({payload.total_count}건)",
            )

            if not result.get("ok"):
                logger.error(f"Slack API 오류: {result.get('error')}")
                return False

            thread_ts = result.get("ts")
            logger.info(f"Slack 알림 전송 성공: 페이지 1/{total_pages}")

            # 나머지 페이지 (11~20, 21~30, ...) → 스레드 reply
            for page in range(1, total_pages):
                is_last_page = (page == total_pages - 1)
                include_deadline_page = is_last_page and payload.deadline_soon
                page_blocks = self._build_blocks_page(
                    payload, page=page, include_deadline=include_deadline_page,
                )
                page_result = self._post_message(
                    page_blocks,
                    text=f"지원사업 공고 알림 (계속 {page + 1}/{total_pages})",
                    thread_ts=thread_ts,
                )
                if not page_result.get("ok"):
                    logger.error(f"Slack API 오류 (페이지 {page + 1}): {page_result.get('error')}")
                    return False
                logger.info(f"Slack 알림 전송 성공: 페이지 {page + 1}/{total_pages}")

            # 마감 임박이 있고 2페이지 이상이면 별도 스레드 메시지
            if payload.deadline_soon and total_pages > 1:
                deadline_blocks = self._build_deadline_blocks(payload)
                dl_result = self._post_message(
                    deadline_blocks,
                    text=f"마감 임박 공고 ({len(payload.deadline_soon)}건)",
                    thread_ts=thread_ts,
                )
                if not dl_result.get("ok"):
                    logger.error(f"Slack API 오류 (마감 임박): {dl_result.get('error')}")

            logger.info(f"Slack 알림 전송 완료: 총 {payload.total_count}건")
            return True

        except requests.RequestException as e:
            logger.error(f"Slack 전송 오류: {e}")
            return False

    def _build_blocks_page(
        self,
        payload: NotificationPayload,
        page: int,
        include_deadline: bool = False,
    ) -> list[dict]:
        """페이지별 Slack Block Kit 메시지 생성"""
        blocks: list[dict] = []
        total_new = len(payload.new_announcements)
        total_pages = max(math.ceil(total_new / PAGE_SIZE), 1)

        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total_new)
        page_announcements = payload.new_announcements[start:end]

        if page == 0:
            # 첫 페이지: 헤더 포함
            today = datetime.now().strftime("%Y-%m-%d")
            if total_new == 0 and payload.deadline_soon:
                header_text = f"⏰ 마감 리마인더 ({today})"
            else:
                header_text = f"📢 지원사업 공고 알림 ({today})"
            blocks.append({
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header_text,
                    "emoji": True,
                },
            })
            blocks.append({"type": "divider"})

        # 신규 공고 섹션
        if page_announcements:
            if page == 0:
                title_text = f"*🆕 신규 공고 ({total_new}건)*"
            else:
                title_text = f"*🆕 신규 공고 (계속 {start + 1}~{end}/{total_new}건)*"

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": title_text},
            })

            for ann in page_announcements:
                blocks.append(self._announcement_block(ann))

            blocks.append({"type": "divider"})

        # 마감 임박 (첫 페이지 1장짜리 또는 마지막 페이지)
        if include_deadline and payload.deadline_soon:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔴 마감 임박 ({len(payload.deadline_soon)}건)*",
                },
            })
            for ann in payload.deadline_soon:
                blocks.append(self._announcement_block(ann, highlight_deadline=True))
            blocks.append({"type": "divider"})

        # 푸터
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "🤖 Gov Funding Monitor | K-Startup, 기업마당, NIPA, NIA, IITP에서 수집",
            }],
        })

        return blocks

    def _build_deadline_blocks(self, payload: NotificationPayload) -> list[dict]:
        """마감 임박 전용 블록 (멀티페이지 시 별도 스레드 메시지)"""
        blocks: list[dict] = []
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🔴 마감 임박 ({len(payload.deadline_soon)}건)*",
            },
        })
        for ann in payload.deadline_soon:
            blocks.append(self._announcement_block(ann, highlight_deadline=True))
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": "🤖 Gov Funding Monitor | K-Startup, 기업마당, NIPA, NIA, IITP에서 수집",
            }],
        })
        return blocks

    def _announcement_block(
        self,
        ann: Announcement,
        highlight_deadline: bool = False,
    ) -> dict:
        """단일 공고 블록 생성"""
        # 제목 및 링크
        title_text = f"<{ann.url}|{ann.title}>"

        # 메타 정보
        meta_parts = []
        source_name = SOURCE_DISPLAY_NAMES.get(ann.source.value, ann.source.value)
        meta_parts.append(f"출처: {source_name}")
        if ann.category:
            meta_parts.append(f"분야: {ann.category}")
        if ann.d_day is not None:
            d_day_text = f"D-{ann.d_day}" if ann.d_day > 0 else "마감"
            if highlight_deadline and ann.d_day <= 3:
                d_day_text = f"*{d_day_text}* 🚨"
            meta_parts.append(f"마감: {d_day_text}")
        if ann.organization:
            meta_parts.append(f"기관: {ann.organization}")

        meta_text = " | ".join(meta_parts) if meta_parts else ""

        # 요약 (있는 경우)
        summary_text = ""
        if ann.summary:
            summary_text = f"\n_{ann.summary}_"

        return {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"• {title_text}\n{meta_text}{summary_text}",
            },
        }


async def send_slack_notification(payload: NotificationPayload) -> bool:
    """편의 함수: Slack 알림 전송"""
    notifier = SlackNotifier()
    return await notifier.send_notification(payload)
