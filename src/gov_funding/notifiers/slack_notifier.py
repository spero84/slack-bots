"""Slack 알림 전송

메시지 구성 규칙:
- 첫 메시지(채널): 헤더 + 신규 공고 1~PAGE_SIZE번 + 마감 임박 섹션 (+ 수집 이상 메모) + 푸터.
  마감 리마인더는 스레드를 열지 않아도 보이도록 항상 첫 메시지에 넣는다.
- 첫 메시지가 Slack 한도(블록 50개, 메시지 글자 수 안전 한도)를 넘으면 남은 마감 임박 공고만
  첫 메시지 바로 다음 스레드 reply로 넘긴다. 이어서 신규 공고 나머지 페이지를 스레드 reply로 보낸다.
  모든 공고는 정확히 한 번만 나간다.
- 크롤링 결과(제목, 기관, 요약)는 신뢰할 수 없는 문자열이므로 mrkdwn 제어문자(&, <, >)를 이스케이프한다.
"""
import html
import math
import re
import time
from collections import Counter
from typing import Callable, Iterable, Optional
from urllib.parse import quote

import requests

from ..storage import Announcement, NotificationPayload
from ..utils import get_config, logger
from ..utils.dates import today_kst
from ..utils.text import clean_text

PAGE_SIZE = 10

# Slack 한도: 메시지당 블록 50개, section text 3000자 (여유분을 두고 2900자로 제한)
MAX_BLOCKS_PER_MESSAGE = 50
MAX_SECTION_CHARS = 2900
# 블록 전체 길이가 너무 길면 msg_blocks_too_long이 날 수 있어 메시지당 텍스트 합계도 제한 (안전 한도)
MAX_MESSAGE_CHARS = 12000
MAX_TITLE_CHARS = 300
MAX_SUMMARY_CHARS = 500
MAX_META_FIELD_CHARS = 100
MAX_OPS_NOTE_CHARS = 1000

# chat.postMessage rate limit 재시도
MAX_POST_ATTEMPTS = 3
DEFAULT_RETRY_AFTER = 1.0
MAX_RETRY_AFTER = 60.0

# 표시 순서 = 중복 제거 우선순위 (K-Startup > Bizinfo > ... > MOTIE)
SOURCE_DISPLAY_NAMES = {
    "kstartup": "K-Startup",
    "bizinfo": "기업마당",
    "nipa": "NIPA",
    "nia": "NIA",
    "iitp": "IITP",
    "jointips": "JOINTIPS",
    "msit": "과기정통부",
    "mss": "중기부",
    "motie": "산업부",
}

FOOTER_PREFIX = "🤖 Gov Funding Monitor"

_WHITESPACE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# 텍스트 포맷 헬퍼 (Gmail 알림에서도 재사용)
# ---------------------------------------------------------------------------

def normalize_plain(text: Optional[str]) -> str:
    """제목/기관명 등 한 줄짜리 텍스트 정리

    HTML 엔티티('&amp;', '&lt;')를 풀고 줄바꿈/연속 공백을 공백 하나로 합친다.
    태그 제거는 하지 않는다 ('AI <바우처> 공고'처럼 꺾쇠가 제목의 일부일 수 있음).
    """
    if not text:
        return ""
    return _WHITESPACE.sub(" ", html.unescape(str(text))).strip()


def truncate(text: str, max_chars: int) -> str:
    """max_chars를 넘으면 잘라내고 '…'를 붙인다 (결과 길이 <= max_chars)"""
    if len(text) <= max_chars:
        return text
    return text[: max(max_chars - 1, 0)].rstrip() + "…"


def escape_mrkdwn(text: Optional[str]) -> str:
    """Slack mrkdwn 제어문자 이스케이프 (&, <, >)"""
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_url(url: str) -> str:
    """<url|label> 안에 넣을 URL 정리

    '|', '<', '>', 공백은 링크 구문을 깨므로 퍼센트 인코딩하고, '&'는 '&amp;'로 바꾼다
    (Slack은 저장 시 URL의 '&'를 '&amp;'로 보관하고 렌더링 시 되돌린다).
    """
    url = re.sub(r"[\s|<>]", lambda m: quote(m.group(0), safe=""), url.strip())
    return url.replace("&", "&amp;")


def slack_link(url: Optional[str], label: str) -> str:
    """mrkdwn 링크 생성: <url|label>

    label은 이스케이프하고, label 안의 '|'는 전각 '｜'로 바꿔 구분자가 하나만 남게 한다.
    URL이 없으면 label만 반환한다.
    """
    safe_label = escape_mrkdwn(label).replace("|", "｜")
    if not url or not url.strip():
        return safe_label
    return f"<{_escape_url(url)}|{safe_label}>"


def format_summary(summary: Optional[str], max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """AI 요약을 이탤릭 한 줄로 변환 ('_요약_'), 내용이 없으면 빈 문자열

    clean_text로 HTML 잔재('</br>', '&nbsp;')를 지우고, 줄바꿈을 공백으로 합치고
    (이탤릭은 여러 줄에 걸칠 수 없음), 이탤릭 구분자 '_'를 제거한 뒤 잘라내고 이스케이프한다.
    """
    text = clean_text(summary).replace("_", "")
    text = _WHITESPACE.sub(" ", text).strip()
    if not text or max_chars <= 0:
        return ""
    return f"_{escape_mrkdwn(truncate(text, max_chars))}_"


def format_d_day(d_day: Optional[int]) -> Optional[str]:
    """D-day 숫자 → 표시 문자열 (None이면 None)"""
    if d_day is None:
        return None
    if d_day == 0:
        return "D-Day(오늘 마감)"
    if d_day > 0:
        return f"D-{d_day}"
    return "마감"


def resolve_d_day(ann: Announcement) -> Optional[int]:
    """공고의 D-day (d_day가 없고 마감일만 있으면 KST 오늘 기준으로 계산, 지난 마감은 음수)"""
    if ann.d_day is not None:
        return ann.d_day
    if ann.deadline is not None:
        return (ann.deadline.date() - today_kst()).days
    return None


def deadline_label(ann: Announcement) -> str:
    """메타 정보의 '마감: …' 값

    D-day가 있으면 'D-3' / 'D-Day(오늘 마감)', 마감일이 지났으면 '마감',
    마감일을 모르면 '미상' (사용자가 직접 확인해야 함을 알림).
    """
    return format_d_day(resolve_d_day(ann)) or "미상"


def _source_value(ann: Announcement) -> str:
    return getattr(ann.source, "value", str(ann.source))


def summarize_sources(announcements: Iterable[Announcement], max_names: int = 3) -> str:
    """공고 출처 표시명 요약 (건수 많은 순, 동률이면 우선순위 순)

    예: '기업마당, K-Startup' / 'K-Startup, 기업마당, NIPA 등 5곳'
    """
    counts = Counter(_source_value(a) for a in announcements)
    if not counts:
        return ""
    order = list(SOURCE_DISPLAY_NAMES)
    ranked = sorted(
        counts,
        key=lambda s: (-counts[s], order.index(s) if s in order else len(order), s),
    )
    names = [SOURCE_DISPLAY_NAMES.get(s, s) for s in ranked]
    if len(names) > max_names:
        return f"{', '.join(names[:max_names])} 등 {len(names)}곳"
    return ", ".join(names)


def _normalize_notes(ops_notes: Optional[Iterable[str]]) -> list[str]:
    """운영 메모 정리 (빈 항목 제거, 한 줄로 정리)"""
    if not ops_notes:
        return []
    return [n for n in (normalize_plain(note) for note in ops_notes) if n]


def _chunk(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _retry_after_seconds(response) -> float:
    """Retry-After 헤더(초) 파싱, 없거나 이상하면 기본 1초"""
    headers = getattr(response, "headers", None) or {}
    value = headers.get("Retry-After") or headers.get("retry-after")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    if math.isnan(seconds):
        return DEFAULT_RETRY_AFTER
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------

class SlackNotifier:
    """Slack Bot Token 기반 알림 전송"""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        channel_id: Optional[str] = None,
        sleep_func: Optional[Callable[[float], None]] = None,
    ):
        config = get_config()
        self.bot_token = bot_token or config.slack_bot_token
        self.channel_id = channel_id or config.slack_channel_id
        self.api_url = "https://slack.com/api/chat.postMessage"
        # 테스트에서 대기 없이 재시도를 검증할 수 있도록 sleep 주입 가능
        self._sleep_func = sleep_func

    def _sleep(self, seconds: float) -> None:
        (self._sleep_func or time.sleep)(seconds)

    def _post_message(
        self,
        blocks: list[dict],
        text: str,
        thread_ts: Optional[str] = None,
    ) -> dict:
        """Slack chat.postMessage 호출 (rate limit 시 Retry-After만큼 대기 후 최대 3회 시도)

        Returns:
            Slack API 응답 dict. HTTP 429는 {"ok": False, "error": "ratelimited"}로 변환한다.
        """
        body: dict = {
            "channel": self.channel_id,
            "blocks": blocks,
            "text": text,
            # 공고 사이트 미리보기 카드가 메시지마다 붙지 않도록 링크 펼치기 비활성화
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if thread_ts:
            body["thread_ts"] = thread_ts

        result: dict = {}
        for attempt in range(1, MAX_POST_ATTEMPTS + 1):
            response = requests.post(
                self.api_url,
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=body,
                timeout=30,
            )
            if getattr(response, "status_code", 200) == 429:
                result = {"ok": False, "error": "ratelimited"}
            else:
                result = response.json()

            if result.get("error") != "ratelimited":
                return result
            if attempt == MAX_POST_ATTEMPTS:
                logger.error(f"Slack rate limit - {MAX_POST_ATTEMPTS}회 시도 모두 실패")
                return result

            wait = _retry_after_seconds(response)
            logger.warning(
                f"Slack rate limit - {wait:g}초 후 재시도 ({attempt}/{MAX_POST_ATTEMPTS - 1})"
            )
            self._sleep(wait)
        return result

    async def send_notification(
        self,
        payload: NotificationPayload,
        ops_notes: Optional[list[str]] = None,
    ) -> bool:
        """알림 전송 (PAGE_SIZE개씩 페이지네이션, 2번째 메시지부터 스레드 reply)

        Args:
            payload: 알림 페이로드
            ops_notes: 수집 이상 메모 (예: 'jointips: 0건'). 있으면 첫 메시지에 context 블록으로 붙이고,
                보낼 공고가 없으면 수집 이상 메시지만 따로 보낸다.

        Returns:
            모든 메시지 전송에 성공했거나 보낼 내용이 없으면 True, 하나라도 실패하면 False
        """
        messages = self.build_messages(payload, ops_notes=ops_notes)
        if not messages:
            logger.info("알림 내용 없음 - 전송 스킵")
            return True

        if not self.bot_token or not self.channel_id:
            logger.error("Slack 설정 누락 (SLACK_BOT_TOKEN, SLACK_CHANNEL_ID)")
            return False

        total = len(messages)
        thread_ts: Optional[str] = None
        try:
            for index, (blocks, text) in enumerate(messages, start=1):
                result = self._post_message(blocks, text=text, thread_ts=thread_ts)
                if not result.get("ok"):
                    logger.error(f"Slack API 오류 (메시지 {index}/{total}): {result.get('error')}")
                    return False
                if index == 1:
                    thread_ts = result.get("ts")
                    if not thread_ts and total > 1:
                        logger.warning("Slack 응답에 ts 없음 - 이후 메시지는 스레드가 아닌 채널에 전송")
                logger.info(f"Slack 메시지 전송 성공: {index}/{total}")
        except (requests.RequestException, ValueError) as e:
            logger.error(f"Slack 전송 오류: {e}")
            return False

        logger.info(
            f"Slack 알림 전송 완료: 신규 {len(payload.new_announcements)}건, "
            f"마감 임박 {len(payload.deadline_soon)}건 (메시지 {total}개)"
        )
        return True

    # -----------------------------------------------------------------------
    # 메시지 구성
    # -----------------------------------------------------------------------

    def build_messages(
        self,
        payload: NotificationPayload,
        ops_notes: Optional[list[str]] = None,
    ) -> list[tuple[list[dict], str]]:
        """전송할 메시지 목록 생성: [(blocks, fallback text), ...]

        첫 항목은 채널 메시지, 나머지는 첫 메시지의 스레드 reply로 전송된다.
        순서: [헤더 + 신규 1~10 + 마감 임박(들어가는 만큼)] → [마감 임박 나머지] → [신규 11~20] → ...
        """
        new_items = list(payload.new_announcements)
        deadline_items = list(payload.deadline_soon)
        notes = _normalize_notes(ops_notes)

        if not new_items and not deadline_items:
            return [self._build_ops_only_message(notes)] if notes else []

        today = today_kst().strftime("%Y-%m-%d")
        if new_items:
            header_text = f"📢 지원사업 공고 알림 ({today})"
        else:
            header_text = f"⏰ 마감 리마인더 ({today})"

        footer = self._footer_block(new_items + deadline_items)
        new_pages = _chunk(new_items, PAGE_SIZE)

        # 첫 메시지: 헤더 + 신규 첫 페이지 + 마감 임박 (한도 내에서 최대한)
        first_blocks = self._header_blocks(header_text)
        if new_items:
            first_blocks += self._new_section_blocks(new_items, page=0)
        tail = ([self._ops_block(notes)] if notes else []) + [footer]

        deadline_blocks = [
            self._announcement_block(ann, highlight_deadline=True) for ann in deadline_items
        ]
        fit = self._fit_count(deadline_blocks, used=first_blocks + tail)
        if fit:
            first_blocks += self._deadline_section_blocks(deadline_blocks, start=0, end=fit)
        messages: list[tuple[list[dict], str]] = [
            (first_blocks + tail, f"지원사업 공고 알림 ({payload.total_count}건)")
        ]

        # 첫 메시지에 다 못 넣은 마감 임박 → 첫 메시지 바로 다음 스레드 reply
        start = fit
        while start < len(deadline_blocks):
            count = max(self._fit_count(deadline_blocks[start:], used=[footer]), 1)
            end = start + count
            if start == 0:
                text = f"마감 임박 공고 ({len(deadline_blocks)}건)"
            else:
                text = f"마감 임박 공고 (계속 {start + 1}~{end}/{len(deadline_blocks)}건)"
            messages.append((
                self._deadline_section_blocks(deadline_blocks, start=start, end=end) + [footer],
                text,
            ))
            start = end

        # 신규 공고 나머지 페이지 (11~20, 21~30, ...)
        for page in range(1, len(new_pages)):
            messages.append((
                self._new_section_blocks(new_items, page=page) + [footer],
                f"지원사업 공고 알림 (계속 {page + 1}/{len(new_pages)})",
            ))

        return self._split_oversized(messages)

    @staticmethod
    def _blocks_chars(blocks: list[dict]) -> int:
        """블록에 들어간 텍스트 길이 합계"""
        total = 0
        for block in blocks:
            text = block.get("text")
            if isinstance(text, dict):
                total += len(text.get("text", ""))
            for element in block.get("elements", []):
                total += len(element.get("text", ""))
        return total

    def _fit_count(self, item_blocks: list[dict], used: list[dict]) -> int:
        """이미 쓰인 블록(used)과 섹션 제목/구분선을 빼고, 한 메시지에 더 넣을 수 있는 공고 수"""
        block_budget = MAX_BLOCKS_PER_MESSAGE - len(used) - 2  # 섹션 제목 + divider
        char_budget = MAX_MESSAGE_CHARS - self._blocks_chars(used) - 100  # 섹션 제목 여유분
        count = 0
        for block in item_blocks:
            size = self._blocks_chars([block])
            if count + 1 > block_budget or size > char_budget:
                break
            char_budget -= size
            count += 1
        return count

    @staticmethod
    def _split_oversized(messages: list[tuple[list[dict], str]]) -> list[tuple[list[dict], str]]:
        """블록 50개를 넘는 메시지는 여러 메시지로 분할 (PAGE_SIZE 변경 대비 안전장치)"""
        result: list[tuple[list[dict], str]] = []
        for blocks, text in messages:
            if len(blocks) <= MAX_BLOCKS_PER_MESSAGE:
                result.append((blocks, text))
                continue
            chunks = _chunk(blocks, MAX_BLOCKS_PER_MESSAGE)
            for i, chunk in enumerate(chunks, start=1):
                result.append((chunk, text if i == 1 else f"{text} ({i}/{len(chunks)})"))
        return result

    @staticmethod
    def _section(text: str) -> dict:
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": truncate(text, MAX_SECTION_CHARS)},
        }

    @staticmethod
    def _header_blocks(header_text: str) -> list[dict]:
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": truncate(header_text, 150), "emoji": True},
            },
            {"type": "divider"},
        ]

    def _new_section_blocks(self, items: list[Announcement], page: int) -> list[dict]:
        """신규 공고 섹션 (page번째 PAGE_SIZE 묶음)"""
        total = len(items)
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)
        if page == 0:
            title = f"*🆕 신규 공고 ({total}건)*"
        else:
            title = f"*🆕 신규 공고 (계속 {start + 1}~{end}/{total}건)*"
        blocks = [self._section(title)]
        blocks += [self._announcement_block(ann) for ann in items[start:end]]
        blocks.append({"type": "divider"})
        return blocks

    def _deadline_section_blocks(self, item_blocks: list[dict], start: int, end: int) -> list[dict]:
        """마감 임박 섹션 (미리 만든 공고 블록의 [start:end] 구간)"""
        total = len(item_blocks)
        if start == 0 and end >= total:
            title = f"*🔴 마감 임박 ({total}건)*"
        elif start == 0:
            title = f"*🔴 마감 임박 ({total}건 중 1~{end}건, 나머지는 스레드)*"
        else:
            title = f"*🔴 마감 임박 (계속 {start + 1}~{end}/{total}건)*"
        return [self._section(title), *item_blocks[start:end], {"type": "divider"}]

    @staticmethod
    def _footer_block(announcements: list[Announcement]) -> dict:
        """푸터: 이번 알림에 포함된 출처만 표시"""
        sources = summarize_sources(announcements)
        text = f"{FOOTER_PREFIX} | {sources}에서 수집" if sources else FOOTER_PREFIX
        return {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": escape_mrkdwn(text)}],
        }

    @staticmethod
    def _ops_block(notes: list[str]) -> dict:
        """수집 이상 메모 context 블록"""
        joined = truncate(" · ".join(notes), MAX_OPS_NOTE_CHARS)
        return {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"⚠️ 수집 이상: {escape_mrkdwn(joined)}"}],
        }

    def _build_ops_only_message(self, notes: list[str]) -> tuple[list[dict], str]:
        """보낼 공고는 없고 수집 이상만 있을 때의 단독 메시지"""
        today = today_kst().strftime("%Y-%m-%d")
        header_text = f"⚠️ Gov Funding Monitor 수집 이상 ({today})"
        body = truncate("\n".join(f"• {note}" for note in notes), MAX_OPS_NOTE_CHARS)
        blocks = self._header_blocks(header_text)[:1]
        blocks.append(self._section(escape_mrkdwn(body)))
        return blocks, f"Gov Funding Monitor 수집 이상 ({today})"

    def _announcement_block(
        self,
        ann: Announcement,
        highlight_deadline: bool = False,
    ) -> dict:
        """단일 공고 블록 생성 (모든 외부 문자열은 이스케이프)"""
        title = truncate(normalize_plain(ann.title), MAX_TITLE_CHARS) or "(제목 없음)"
        link = slack_link(ann.url, title)

        meta_parts = [f"출처: {SOURCE_DISPLAY_NAMES.get(_source_value(ann), _source_value(ann))}"]
        category = truncate(normalize_plain(ann.category), MAX_META_FIELD_CHARS)
        if category:
            meta_parts.append(f"분야: {escape_mrkdwn(category)}")
        d_day = resolve_d_day(ann)
        d_day_text = deadline_label(ann)
        if highlight_deadline and d_day is not None and 0 <= d_day <= 3:
            d_day_text = f"*{d_day_text}* 🚨"
        meta_parts.append(f"마감: {d_day_text}")
        organization = truncate(normalize_plain(ann.organization), MAX_META_FIELD_CHARS)
        if organization:
            meta_parts.append(f"기관: {escape_mrkdwn(organization)}")
        meta_text = " | ".join(meta_parts)

        head = f"• {link}\n{meta_text}"
        summary_limit = MAX_SUMMARY_CHARS
        summary = format_summary(ann.summary, summary_limit)
        # section text 한도 보호: 요약부터 줄이고, 그래도 넘치면 요약 제거 → 링크 제거
        while summary and len(head) + 1 + len(summary) > MAX_SECTION_CHARS:
            summary_limit //= 2
            summary = format_summary(ann.summary, summary_limit) if summary_limit >= 20 else ""
        if len(head) > MAX_SECTION_CHARS:
            head = f"• {escape_mrkdwn(title)}\n{meta_text}"

        text = f"{head}\n{summary}" if summary else head
        return self._section(text)


async def send_slack_notification(
    payload: NotificationPayload,
    ops_notes: Optional[list[str]] = None,
) -> bool:
    """편의 함수: Slack 알림 전송

    Args:
        payload: 알림 페이로드
        ops_notes: 수집 이상 메모 (선택). 공고가 없어도 메모가 있으면 수집 이상 메시지를 보낸다.

    Returns:
        모든 메시지 전송 성공(또는 보낼 내용 없음) 시 True, 실패 시 False
    """
    notifier = SlackNotifier()
    return await notifier.send_notification(payload, ops_notes=ops_notes)
