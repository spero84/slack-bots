"""Slack 알림 — 국가별로 기사 링크를 정리해 채널에 올린다

메시지 구성:
- 헤더 + 검색 범위/기간/건수
- 🇰🇷 국내 / 🇺🇸 미국 / 🇸🇬 싱가포르 섹션: 같은 소식을 다룬 기사는 대표 기사 1건 + '같은 소식' 링크로 묶는다
- 📎 참고: 건설·EPC 기업의 AI 데이터센터/인프라 수주·투자 기사 (링크만)
- ⚠️ 수집 이상 (검색 실패, AI 판정 실패 등) — 0건이어도 실행 여부를 알 수 있게 항상 보낸다

Slack 메시지는 블록 50개 제한이 있어 넘치면 나머지를 스레드 답글로 보낸다.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import requests

from .config import COMPETITOR_GROUPS, COMPETITORS, KST, OUTLETS, get_config
from .logger import logger
from .models import REGION_LABELS, THEME_LABELS, NewsItem, Region

MAX_BLOCKS = 48           # Slack 한도 50에서 여유
MAX_SECTION_CHARS = 2900  # section text 한도 3000
MAX_SAME_STORY_LINKS = 5  # 보도자료는 매체 10~18곳에 동시에 실린다 — 나머지는 '외 N곳'
WEEKDAYS = "월화수목금토일"


@dataclass
class DigestContext:
    """메시지 머리말/꼬리말에 쓰는 실행 정보"""
    run_at: datetime                    # KST aware
    days_by_region: dict = field(default_factory=dict)   # {Region: 검색 기간(일)}
    hours: Optional[float] = None       # --hours로 기간을 직접 지정한 경우
    candidates: int = 0                 # AI 판정에 넘긴 후보 수
    warnings: list[str] = field(default_factory=list)
    repeats_skipped: int = 0            # 이미 보낸 소식의 다른 매체 기사라 뺀 수

    def period_text(self) -> str:
        if self.hours is not None:
            return f"최근 {self.hours:g}시간"
        groups: dict[int, list[str]] = {}
        for region, days in self.days_by_region.items():
            groups.setdefault(days, []).append(REGION_LABELS[region].split()[1])
        return " · ".join(f"{'/'.join(names)} 최근 {days}일" for days, names in groups.items())


@dataclass
class StoryGroup:
    """같은 소식을 다룬 기사 묶음 (대표 기사 + 나머지)"""
    lead: NewsItem
    others: list[NewsItem] = field(default_factory=list)


def escape_mrkdwn(text: str) -> str:
    """Slack mrkdwn 제어 문자 이스케이프 (&, <, >)"""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_link(url: str, label: str) -> str:
    """<url|label> — label 안의 '|'와 '>'는 링크 문법을 깨므로 바꾼다"""
    safe_label = escape_mrkdwn(label).replace("|", "¦")
    safe_url = (url or "").replace(">", "%3E").replace("|", "%7C").replace(" ", "%20")
    return f"<{safe_url}|{safe_label}>"


def _kst(dt: datetime) -> datetime:
    return dt.astimezone(KST)


def _fmt_day(dt: datetime) -> str:
    d = _kst(dt)
    return f"{d:%m/%d}({WEEKDAYS[d.weekday()]})"


def _outlet_rank(item: NewsItem) -> int:
    keys = list(OUTLETS)
    return keys.index(item.outlet_key) if item.outlet_key in OUTLETS else len(keys)


def group_stories(items: list[NewsItem]) -> list[StoryGroup]:
    """item.story(대표 기사 번호)가 같은 기사를 묶는다. 대표는 점수 → 게시 시각 → 언론사 순서로 고른다."""
    buckets: dict[object, list[NewsItem]] = {}
    for idx, item in enumerate(items):
        key = item.story if item.story is not None else f"solo-{idx}"
        buckets.setdefault(key, []).append(item)
    groups: list[StoryGroup] = []
    for members in buckets.values():
        members.sort(key=lambda a: (-(a.score or 0.0), a.published_at, _outlet_rank(a)))
        groups.append(StoryGroup(lead=members[0], others=members[1:]))
    groups.sort(key=lambda g: (-(g.lead.score or 0.0), -g.lead.published_at.timestamp()))
    return groups


def _story_block(group: StoryGroup) -> dict:
    """대표 기사 + 메타 + 요약 + 같은 소식 링크. 3000자를 넘으면 같은 소식 링크부터 줄인다 (링크 중간에서 자르지 않음)."""
    lead = group.lead
    lines = [f"• *{slack_link(lead.link, lead.title)}*"]
    meta = [escape_mrkdwn(lead.outlet_name), _fmt_day(lead.published_at)]
    if lead.competitor:
        comp = _COMPETITOR_BY_KEY.get(lead.competitor)
        meta.insert(0, f"🏷 {escape_mrkdwn(comp.name if comp else lead.competitor)}")
        if lead.mention == "body":
            meta.append("본문 언급")
    elif lead.theme is not None:
        meta.append(THEME_LABELS[lead.theme])
    lines.append("      " + " · ".join(meta))
    if lead.summary_ko:
        lines.append(f"      _{escape_mrkdwn(lead.summary_ko)}_")
    base = "\n".join(lines)
    others = list(group.others)[:MAX_SAME_STORY_LINKS]
    while others:
        links = " · ".join(slack_link(o.link, o.outlet_name) for o in others)
        dropped = len(group.others) - len(others)
        extra = f" 외 {dropped}곳" if dropped else ""
        candidate = f"{base}\n      같은 소식: {links}{extra}"
        if len(candidate) <= MAX_SECTION_CHARS:
            base = candidate
            break
        others.pop()
    if len(base) > MAX_SECTION_CHARS:   # 대표 기사 하나로도 넘치는 경우 (사실상 없음): 요약을 뺀다
        base = "\n".join(lines[:2])[:MAX_SECTION_CHARS]
    return {"type": "section", "text": {"type": "mrkdwn", "text": base}}


def _infra_blocks(items: list[NewsItem]) -> list[tuple[dict, list[NewsItem]]]:
    """참고 섹션: 국가 표시 + 링크 한 줄씩. 3000자 한도에 맞춰 여러 블록으로 나눈다. [(block, 담긴 기사)]"""
    ordered = sorted(items, key=lambda a: (list(Region).index(a.region), -a.published_at.timestamp()))
    out: list[tuple[dict, list[NewsItem]]] = []
    buf, buf_items = "*📎 참고 — AI 데이터센터·인프라 EPC/건설 동향*", []
    for item in ordered:
        flag = REGION_LABELS[item.region].split()[0]
        line = f"{flag} {slack_link(item.link, item.title)} — {escape_mrkdwn(item.outlet_name)}"
        if buf and len(buf) + len(line) + 1 > MAX_SECTION_CHARS:
            out.append(({"type": "section", "text": {"type": "mrkdwn", "text": buf}}, buf_items))
            buf, buf_items = "", []
        buf = f"{buf}\n{line}" if buf else line[:MAX_SECTION_CHARS]
        buf_items.append(item)
    if buf:
        out.append(({"type": "section", "text": {"type": "mrkdwn", "text": buf}}, buf_items))
    return out


_COMPETITOR_BY_KEY = {c.key: c for c in COMPETITORS}


def outlet_scope_text() -> str:
    counts = {r: sum(1 for o in OUTLETS.values() if o.region == r) for r in Region}
    return " · ".join(f"{REGION_LABELS[r].split()[1]} {n}개" for r, n in counts.items() if n)


def _competitor_body(items: list[NewsItem]) -> list[tuple[dict, list[NewsItem]]]:
    """🏁 경쟁사 동향: 그룹(건설 스타트업 국내/글로벌, RAG 국내)별 소제목 + 소식"""
    body: list[tuple[dict, list[NewsItem]]] = []
    by_group: dict[str, list[NewsItem]] = {}
    for item in items:
        comp = _COMPETITOR_BY_KEY.get(item.competitor or "")
        by_group.setdefault(comp.group if comp else "", []).append(item)
    group_order = list(COMPETITOR_GROUPS) + [g for g in by_group if g not in COMPETITOR_GROUPS]
    stories = {g: group_stories(by_group[g]) for g in group_order if by_group.get(g)}
    total = sum(len(v) for v in stories.values())
    body.append(({"type": "divider"}, []))
    body.append(({**_section(f"*🏁 경쟁사 동향* ({total}건)"), "_label": True}, []))
    for g, groups in stories.items():
        label = COMPETITOR_GROUPS.get(g, "기타")
        body.append(({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{label}*"}], "_label": True}, []))
        body.extend((_story_block(st), [st.lead, *st.others]) for st in groups)
    return body


@dataclass
class SlackMessage:
    """보낼 메시지 1개와 그 안에 담긴 기사 (전송 성공한 메시지의 기사만 '보냄'으로 기록한다)"""
    blocks: list[dict]
    text: str
    items: list[NewsItem] = field(default_factory=list)


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _is_label(block: dict) -> bool:
    """뒤따르는 기사 없이 메시지 끝에 혼자 남으면 안 되는 블록 (구분선, 국가/참고 제목)"""
    return block.get("type") == "divider" or bool(block.get("_label"))


def _pack(header: list[dict], body: list[tuple[dict, list[NewsItem]]], footer: list[dict],
          fallback: str) -> list[SlackMessage]:
    """블록 50개 한도로 나눈다. 첫 메시지 = header + body 앞부분 + footer, 나머지는 스레드 답글.
    나눈 자리 끝에 구분선·국가 제목만 남으면 다음 메시지로 넘긴다."""
    messages: list[SlackMessage] = []
    budget = MAX_BLOCKS - len(header) - len(footer)
    rest = list(body)
    first = True
    while True:
        chunk = rest[:budget]
        if len(chunk) < len(rest):
            while chunk and _is_label(chunk[-1][0]):
                chunk.pop()
            if not chunk:            # 제목만 잔뜩 이어지는 비정상 입력 — 그냥 자른다
                chunk = rest[:budget]
        rest = rest[len(chunk):]
        blocks = [{k: v for k, v in b.items() if k != "_label"} for b, _ in chunk]
        items = [i for _, its in chunk for i in its]
        if first:
            messages.append(SlackMessage(header + blocks + footer, fallback, items))
            first = False
        else:
            messages.append(SlackMessage(blocks, f"{fallback} (계속)", items))
        if not rest:
            break
        budget = MAX_BLOCKS
    return messages


def build_messages(
    main_items: list[NewsItem],
    infra_items: list[NewsItem],
    ctx: DigestContext,
    competitors: Optional[list[NewsItem]] = None,
) -> list[SlackMessage]:
    """Slack 메시지 목록 — 첫 메시지가 본문, 나머지는 스레드 답글

    순서: 🏁 경쟁사 동향 → 국가별 주제 기사 → 📎 참고
    """
    competitors = competitors or []
    groups_by_region = {r: group_stories([i for i in main_items if i.region == r]) for r in Region}
    story_count = sum(len(g) for g in groups_by_region.values())
    article_count = len(main_items)
    comp_body = _competitor_body(competitors) if competitors else []
    comp_count = len(group_stories(competitors)) if competitors else 0

    title = f"📰 EPC·AEC × AI 뉴스 — {_fmt_day(ctx.run_at)}"
    lines = []
    if comp_count:
        lines.append(f"*🏁 경쟁사 소식 {comp_count}건*")
    if story_count:
        per_region = " · ".join(
            f"{REGION_LABELS[r]} {len(g)}" for r, g in groups_by_region.items() if g
        )
        lines.append(f"*새 소식 {story_count}건* (기사 {article_count}개) — {per_region}")
    elif comp_count:
        lines.append("주제 기사: 새 소식 없음")
    else:
        lines.append("*새 소식 없음* — 검색 범위에서 주제에 맞는 새 기사를 찾지 못했습니다.")
    if infra_items:
        lines.append(f"📎 참고(AI 인프라 EPC/건설) {len(infra_items)}건")
    summary = "\n".join(lines)

    header = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        _section(summary),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": (
            f"검색 기간 {ctx.period_text()} · 언론사 {outlet_scope_text()}"
            + (f" · 경쟁사 {len(COMPETITORS)}곳" if COMPETITORS else "")
            + " · 키워드 EPC·AEC × AI/RAG, AI × 도면·BIM"
        )}]},
    ]

    body: list[tuple[dict, list[NewsItem]]] = list(comp_body)
    for region, groups in groups_by_region.items():
        if not groups:
            continue
        body.append(({"type": "divider"}, []))
        body.append(({**_section(f"*{REGION_LABELS[region]}* ({len(groups)}건)"), "_label": True}, []))
        body.extend((_story_block(g), [g.lead, *g.others]) for g in groups)
    if infra_items:
        body.append(({"type": "divider"}, []))
        body.extend(_infra_blocks(infra_items))
    empty = [REGION_LABELS[r] for r in (ctx.days_by_region or Region) if not groups_by_region.get(r)]
    if story_count and empty:
        body.append(({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"{' · '.join(empty)}: 새 기사 없음"}]}, []))

    footer: list[dict] = []
    if ctx.repeats_skipped:
        footer.append({"type": "context", "elements": [{"type": "mrkdwn", "text": (
            f"ℹ️ 이미 보냈거나 위 경쟁사 섹션과 같은 소식을 다른 매체가 쓴 기사 {ctx.repeats_skipped}건은 뺐습니다")}]})
    if ctx.warnings:
        warn_text = "\n".join(f"⚠️ {escape_mrkdwn(w)}" for w in ctx.warnings)[:MAX_SECTION_CHARS]
        footer.append({"type": "context", "elements": [{"type": "mrkdwn", "text": warn_text}]})

    fallback = f"EPC·AEC × AI 뉴스 {_fmt_day(ctx.run_at)} — 새 소식 {story_count}건" + (
        f" · 경쟁사 {comp_count}건" if comp_count else "")
    return _pack(header, body, footer, fallback)


class SlackNotifier:
    """chat.postMessage로 메시지를 보낸다"""

    api_url = "https://slack.com/api/chat.postMessage"
    max_attempts = 3
    max_retry_after = 30

    def __init__(self, bot_token: Optional[str] = None, channel_id: Optional[str] = None,
                 sleep: Callable[[float], None] = time.sleep):
        config = get_config()
        self.bot_token = bot_token or config.slack_bot_token
        self.channel_id = channel_id or config.slack_channel_id
        self._sleep = sleep

    def _post(self, blocks: list[dict], text: str, thread_ts: Optional[str] = None) -> dict:
        """429(Retry-After)·5xx·네트워크 오류는 최대 3번까지 시도. 마지막 오류는 예외 또는 ok=false로 돌려준다."""
        body: dict = {
            "channel": self.channel_id,
            "blocks": blocks,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if thread_ts:
            body["thread_ts"] = thread_ts
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.post(
                    self.api_url,
                    headers={"Authorization": f"Bearer {self.bot_token}",
                             "Content-Type": "application/json; charset=utf-8"},
                    json=body,
                    timeout=30,
                )
            except requests.RequestException:
                if attempt == self.max_attempts:
                    raise
                self._sleep(5 * attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.max_attempts:
                    response.raise_for_status()
                try:
                    wait = float(response.headers.get("Retry-After", 5 * attempt))
                except (TypeError, ValueError):
                    wait = 5.0 * attempt
                logger.warning(f"Slack HTTP {response.status_code} — {min(wait, self.max_retry_after):g}초 뒤 재시도")
                self._sleep(min(max(wait, 1.0), self.max_retry_after))
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("unreachable")

    def send(self, messages: list[SlackMessage]) -> list[int]:
        """보낸 메시지 번호 목록. 본문(0번)이 실패하면 빈 목록 (스레드 답글은 본문이 있어야 보낸다)."""
        if not self.bot_token or not self.channel_id:
            logger.error("Slack 설정 누락 (SLACK_BOT_TOKEN, AEC_NEWS_CHANNEL_ID)")
            return []
        if not messages:
            return []
        try:
            result = self._post(messages[0].blocks, messages[0].text)
        except requests.RequestException as e:
            logger.error(f"Slack 전송 오류: {e}")
            return []
        if not result.get("ok"):
            logger.error(f"Slack API 오류: {result.get('error')} {result.get('response_metadata', '')}")
            return []
        delivered = [0]
        thread_ts = result.get("ts")
        for idx, message in enumerate(messages[1:], 1):
            try:
                reply = self._post(message.blocks, message.text, thread_ts=thread_ts)
            except requests.RequestException as e:
                logger.error(f"Slack 스레드 전송 오류 (메시지 {idx}): {e} — 담긴 기사는 다음 실행에서 다시 보낸다")
                continue
            if reply.get("ok"):
                delivered.append(idx)
            else:
                logger.error(f"Slack 스레드 전송 오류 (메시지 {idx}): {reply.get('error')} — 다음 실행에서 다시 보낸다")
        logger.info(f"Slack 전송 완료 (메시지 {len(delivered)}/{len(messages)}개)")
        return delivered
