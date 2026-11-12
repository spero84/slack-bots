"""Google News RSS 검색 + 원문 URL 복원

검색: https://news.google.com/rss/search?q=...&hl=..&gl=..&ceid=..
- 쿼리 = `{검색어} (site:a OR site:b ...) when:Nd`. q가 200자를 넘으면 Google이 뒤를 조용히 잘라
  `when:`이 빠지고 수년 전 기사가 돌아온다 (2026-11-12 실측). build_queries()가 언론사를 묶어 190자 이하로 만든다.
- 결과는 관련도순 최대 100건이고, 짧은 쿼리에서도 오래된 기사가 가끔 섞여 게시 시각은 코드에서 다시 거른다.
- <source url>의 호스트로 언론사를 판별한다 (표시 이름은 partners.wsj.com도 'WSJ'라 믿을 수 없다).

원문 URL: RSS 링크(news.google.com/rss/articles/CBMi...)는 JS 리다이렉트라 따라가도 원문에 닿지 않는다.
GET /rss/articles/{id} 페이지의 data-n-a-sg/data-n-a-ts 서명으로 batchexecute(Fbv4je)를 호출해 받는다.
비공개 API라 언제든 바뀔 수 있으므로 실패하면 Google News 링크를 그대로 보낸다.

요청은 전부 순차로, 3초+지터 간격. 429/503/google.com/sorry/동의 페이지를 받으면 그 실행에서는
Google 호출을 모두 멈춘다 (재시도하면 차단이 길어진다).
"""
import base64
import calendar
import html
import json
import math
import random
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import urlencode, urlsplit

import feedparser
import requests

from .config import (
    DROP_PATH_PREFIXES,
    HEALTH_TERMS,
    MAX_QUERY_CHARS,
    OUTLETS,
    REGION_SEARCH,
    SEARCH_KIND_THEME,
    SEARCH_TERMS,
    Outlet,
    get_config,
)
from .logger import logger
from .models import NewsItem, Region

SEARCH_URL = "https://news.google.com/rss/search"
ARTICLE_URL = "https://news.google.com/rss/articles/{aid}"   # /read/ 경로는 실측 중 429(sorry)를 받았다
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
TIMEOUT = (5, 20)
RETRY_BACKOFF = (10, 30)
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="(\d+)"')
_DECODE_REQ = (
    '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,'
    'null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts},"{sg}"]'
)
_HANGUL_RE = re.compile(r"[가-힣]")
_BAD_TITLE_RE = re.compile(r"^(untitled|속보서비스|.{0,3})$", re.IGNORECASE)
_STALE_CANARY_MIN_ITEMS = 5


class GoogleThrottled(RuntimeError):
    """429/503/sorry/동의 페이지 — 이번 실행의 Google 호출을 모두 멈춘다"""


# --- 쿼리 만들기 ---

@dataclass(frozen=True)
class SearchQuery:
    kind: str                    # drawing / aec_ai / rag / health
    region: Region
    q: str
    outlet_keys: tuple[str, ...]
    days: int

    @property
    def url(self) -> str:
        rs = REGION_SEARCH[self.region]
        return f"{SEARCH_URL}?{urlencode({'q': self.q, 'hl': rs.hl, 'gl': rs.gl, 'ceid': rs.ceid})}"


def _compose(terms: str, outlets: list[Outlet], days: int) -> str:
    sites = " OR ".join(f"site:{o.domain}" for o in outlets)
    group = f"({sites})" if len(outlets) > 1 else sites
    return f"{terms} {group} when:{days}d"


def pack_outlets(terms: str, outlets: list[Outlet], days: int, max_chars: int = MAX_QUERY_CHARS) -> list[list[Outlet]]:
    """언론사를 순서대로 담다가 q가 max_chars를 넘으면 다음 묶음으로 (언론사 1곳도 못 담으면 ValueError)"""
    groups: list[list[Outlet]] = []
    current: list[Outlet] = []
    for outlet in outlets:
        if len(_compose(terms, current + [outlet], days)) <= max_chars:
            current.append(outlet)
            continue
        if not current:
            raise ValueError(f"검색어가 너무 길다 ({terms!r} + site:{outlet.domain})")
        groups.append(current)
        current = [outlet]
        if len(_compose(terms, current, days)) > max_chars:
            raise ValueError(f"검색어가 너무 길다 ({terms!r} + site:{outlet.domain})")
    if current:
        groups.append(current)
    return groups


def build_queries(days_by_region: dict[Region, int]) -> list[SearchQuery]:
    """주제 × 국가 × 언론사 묶음 쿼리 + 국가별 점검 쿼리"""
    queries: list[SearchQuery] = []
    for region, days in days_by_region.items():
        outlets = [o for o in OUTLETS.values() if o.region == region]
        if not outlets:
            continue
        for kind, terms_by_region in SEARCH_TERMS.items():
            terms = terms_by_region.get(region)
            if not terms:
                continue
            for group in pack_outlets(terms, outlets, days):
                queries.append(SearchQuery(kind, region, _compose(terms, group, days),
                                           tuple(o.key for o in group), days))
        if region in HEALTH_TERMS:
            terms, keys = HEALTH_TERMS[region]
            group = [OUTLETS[k] for k in keys if k in OUTLETS]
            if group:
                queries.append(SearchQuery("health", region, _compose(terms, group, 1),
                                           tuple(o.key for o in group), 1))
    return queries


# --- HTTP 클라이언트 (간격 제한 + 차단 감지) ---

class GoogleNewsClient:
    """news.google.com 요청을 한 줄로 세운다 (간격, 재시도, 차단 시 즉시 중단)"""

    def __init__(self, session: Optional[requests.Session] = None, interval: Optional[float] = None,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.interval = get_config().request_interval if interval is None else interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: Optional[float] = None
        self.tripped: Optional[str] = None     # 차단 사유 (있으면 이후 호출 전부 거부)
        self.requests = 0

    def _wait(self):
        if self._last is not None and self.interval > 0:
            gap = self.interval + random.uniform(0, 1.0)
            remaining = self._last + gap - self._monotonic()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._monotonic()

    def _check_block(self, resp: requests.Response):
        final = resp.url or ""
        reason = None
        if resp.status_code in (429, 503):
            reason = f"HTTP {resp.status_code}"
        elif "google.com/sorry" in final:
            reason = "google.com/sorry (captcha)"
        elif "consent.google." in final:
            reason = "동의(consent) 페이지"
        elif resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", "") \
                and "/rss/search" in final and ("unusual traffic" in resp.text or "Before you continue" in resp.text):
            reason = "비정상 트래픽 안내 페이지"
        if reason:
            self.tripped = reason
            logger.error(f"Google News 차단 감지: {reason} — 이번 실행의 Google 호출을 중단")
            raise GoogleThrottled(reason)

    def request(self, method: str, url: str, *, paced: bool = True, **kwargs) -> requests.Response:
        """네트워크 오류·5xx(503 제외)는 10초, 30초 뒤 두 번 재시도. 차단 신호는 재시도하지 않는다."""
        if self.tripped:
            raise GoogleThrottled(self.tripped)
        attempts = len(RETRY_BACKOFF) + 1
        for attempt in range(attempts):
            if paced:
                self._wait()
            try:
                self.requests += 1
                resp = self.session.request(method, url, timeout=TIMEOUT, **kwargs)
            except requests.RequestException as e:
                if attempt == attempts - 1:
                    raise
                logger.warning(f"Google News 요청 오류 ({e.__class__.__name__}) — {RETRY_BACKOFF[attempt]}초 뒤 재시도")
                self._sleep(RETRY_BACKOFF[attempt])
                continue
            self._check_block(resp)
            if resp.status_code >= 500 and attempt < attempts - 1:
                logger.warning(f"Google News HTTP {resp.status_code} — {RETRY_BACKOFF[attempt]}초 뒤 재시도")
                self._sleep(RETRY_BACKOFF[attempt])
                continue
            return resp
        raise RuntimeError("unreachable")

    # --- 원문 URL 복원 ---

    def decode(self, google_url: str) -> Optional[str]:
        """news.google.com/rss/articles/... → 원문 URL. 실패하면 None (차단이면 GoogleThrottled)."""
        aid = article_id(google_url)
        if aid is None:
            return None
        legacy = legacy_decode(aid)
        if legacy:
            return legacy
        try:
            page = self.request("GET", ARTICLE_URL.format(aid=aid))
            if page.status_code != 200:
                logger.warning(f"원문 URL 복원: 기사 페이지 HTTP {page.status_code}")
                return None
            sg, ts = _SG_RE.search(page.text), _TS_RE.search(page.text)
            if not (sg and ts):
                logger.warning("원문 URL 복원: 서명(data-n-a-sg/ts)이 없음 — 페이지 구조 변경 가능성")
                return None
            payload = json.dumps([[["Fbv4je", _DECODE_REQ.format(aid=aid, ts=ts.group(1), sg=sg.group(1)),
                                    None, "generic"]]])
            # 서명은 이 GET의 ts에 묶여 있어 바로 이어서 POST한다 (간격 대기 없음)
            resp = self.request("POST", BATCH_URL, paced=False, data={"f.req": payload},
                                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
            if resp.status_code != 200:
                logger.warning(f"원문 URL 복원: batchexecute HTTP {resp.status_code}")
                return None
            return parse_batchexecute(resp.text)
        except GoogleThrottled:
            raise
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"원문 URL 복원 실패: {e}")
            return None


def article_id(url: str) -> Optional[str]:
    parts = urlsplit(url or "")
    if parts.hostname != "news.google.com":
        return None
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) >= 2 and segs[-2] in ("articles", "read"):
        return segs[-1]
    return None


def legacy_decode(aid: str) -> Optional[str]:
    """2024년 이전 ID(base64 protobuf에 URL이 그대로 있음). 지금 ID('AU_yqL...')는 None."""
    try:
        raw = base64.urlsafe_b64decode(aid + "=" * (-len(aid) % 4))
    except (ValueError, TypeError):
        return None
    if not raw.startswith(b"\x08\x13\x22"):
        return None
    i, length, shift = 3, 0, 0
    while i < len(raw):
        b = raw[i]
        i += 1
        length |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            break
    payload = raw[i:i + length]
    if payload.startswith(b"AU_yqL"):
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text if text.startswith(("http://", "https://")) else None


def parse_batchexecute(body: str) -> Optional[str]:
    """)]}' 접두어 제거 → ["wrb.fr","Fbv4je","[\\"garturlres\\",\\"<url>\\",...]",...] 의 index 1 (AMP인 index 3은 쓰지 않는다)"""
    start = body.find("[")
    if start < 0:
        return None
    envelope = json.loads(body[start:])
    for entry in envelope:
        if isinstance(entry, list) and len(entry) > 2 and entry[:2] == ["wrb.fr", "Fbv4je"]:
            if not entry[2]:
                return None   # 서명 거부
            inner = json.loads(entry[2])
            if isinstance(inner, list) and len(inner) > 1 and inner[0] == "garturlres" \
                    and isinstance(inner[1], str) and inner[1].startswith("http"):
                return inner[1]
    return None


# --- 피드 파싱 + 1차 필터 ---

def match_outlet(host: str, region: Region) -> Optional[Outlet]:
    """<source url> 호스트 → 언론사. keep_hosts에 정확히 있는 호스트만 (하위 도메인·영문판은 None)."""
    host = (host or "").lower()
    for outlet in OUTLETS.values():
        if outlet.region == region and host in outlet.keep_hosts:
            return outlet
    return None


def clean_title(title: str, source_title: str = "") -> str:
    """NFC 정규화, ' - 언론사' 접미사 반복 제거 (머니투데이는 '- 머니투데이 - 머니투데이'로 두 번 붙는다)"""
    text = unicodedata.normalize("NFC", html.unescape(title or "")).strip()
    text = re.sub(r"\s+", " ", text)
    suffixes = [s for s in {source_title.strip()} if s]
    changed = True
    while changed and suffixes:
        changed = False
        for name in suffixes:
            tail = f" - {name}"
            if text.endswith(tail):
                text = text[: -len(tail)].rstrip()
                changed = True
    return text


def _snippet(description: str, title: str, source_title: str) -> str:
    """RSS description(HTML: 제목 링크 + 언론사)은 대개 제목 반복이라, 제목·언론사를 뺀 나머지만 남긴다"""
    text = re.sub(r"<[^>]+>", " ", description or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    for part in (title, source_title):
        if part:
            text = text.replace(part, " ")
    text = re.sub(r"\s+", " ", text).strip(" -·|")
    return text if len(text) > 15 else ""


def _published(entry) -> Optional[datetime]:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)


@dataclass
class QueryStat:
    region: str
    kind: str
    outlets: str
    status: str = "ok"
    raw: int = 0
    kept: int = 0
    stale: int = 0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def parse_feed(content: bytes, query: SearchQuery, window_start: datetime, now: datetime,
               stat: QueryStat) -> list[NewsItem]:
    """RSS → NewsItem. 언론사 호스트·게시 시각·제목 언어로 거른다."""
    feed = feedparser.parse(content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise ValueError(f"RSS 파싱 실패: {getattr(feed, 'bozo_exception', '')}")
    items: list[NewsItem] = []
    # when: 무시 감지는 실제로 보낸 when:Nd 기준 (--hours로 게시 시각 필터를 좁혀도 오탐하지 않게)
    stale_cutoff = now - timedelta(days=query.days, hours=12)
    stat.raw = len(feed.entries)
    for entry in feed.entries:
        source = entry.get("source") or {}
        host = urlsplit(source.get("href") or "").hostname or ""
        published = _published(entry)
        if published is not None and published < stale_cutoff:
            stat.stale += 1
        if query.kind == "health":
            continue
        outlet = match_outlet(host, query.region)
        if outlet is None:
            continue
        if published is None or not (window_start <= published <= now + timedelta(hours=1)):
            continue
        source_title = source.get("title") or ""
        title = clean_title(entry.get("title", ""), source_title)
        if _BAD_TITLE_RE.match(title):
            continue
        has_hangul = bool(_HANGUL_RE.search(title))
        if (query.region == Region.KR) != has_hangul:
            continue   # 국내 검색의 영어 제목(영문판) / 해외 검색의 한글 제목은 버린다
        google_id = entry.get("id") or article_id(entry.get("link", "")) or entry.get("link", "")
        items.append(NewsItem(
            title=title,
            outlet_key=outlet.key,
            outlet_name=outlet.name_for(host),
            region=query.region,
            published_at=published,
            google_id=google_id,
            google_url=entry.get("link", ""),
            host=host,
            snippet=_snippet(entry.get("description", ""), title, source_title),
            query_themes=[SEARCH_KIND_THEME[query.kind]] if query.kind in SEARCH_KIND_THEME else [],
        ))
    stat.kept = len(items)
    return items


# --- 전체 검색 ---

@dataclass
class SearchResult:
    items: list[NewsItem] = field(default_factory=list)
    stats: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed: int = 0
    total: int = 0

    @property
    def all_failed(self) -> bool:
        return self.total > 0 and self.failed >= self.total

    @property
    def complete(self) -> bool:
        return self.failed == 0


def lookback_days(region: Region, last_success: Optional[datetime], now: datetime, config) -> int:
    """국가별 검색 기간(일). 기본 국내 2일·해외 7일, 마지막 성공 후 더 지났으면 늘린다 (최대 7일)."""
    base = config.kr_lookback_days if region == Region.KR else config.intl_lookback_days
    if last_success is not None:
        gap_days = math.ceil(max(0.0, (now - last_success).total_seconds()) / 86400) + 1
        base = max(base, gap_days)
    return max(1, min(config.max_lookback_days, base))


def search_all(now: datetime, days_by_region: dict[Region, int], hours: Optional[float] = None,
               client: Optional[GoogleNewsClient] = None) -> SearchResult:
    """모든 쿼리를 순서대로 실행. 쿼리 하나의 실패는 건너뛰고, 차단이면 나머지를 모두 건너뛴다.

    hours를 주면 모든 국가의 게시 시각 필터를 now - hours로 맞춘다 (when:은 올림한 일수).
    """
    client = client or GoogleNewsClient()
    queries = build_queries(days_by_region)
    result = SearchResult(total=len(queries))
    health_zero: list[str] = []
    stale_queries: list[str] = []
    for query in queries:
        stat = QueryStat(region=query.region.value, kind=query.kind, outlets=",".join(query.outlet_keys))
        result.stats.append(stat.as_dict())
        window_start = now - (timedelta(hours=hours) if hours is not None else timedelta(days=query.days, hours=2))
        if client.tripped:
            stat.status = "skipped"
            result.failed += 1
            result.stats[-1] = stat.as_dict()
            continue
        try:
            resp = client.request("GET", query.url)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            items = parse_feed(resp.content, query, window_start, now, stat)
            result.items.extend(items)
        except GoogleThrottled:
            stat.status = "throttled"
            result.failed += 1
        except Exception as e:  # noqa: BLE001 — 쿼리 하나의 실패로 전체를 멈추지 않는다
            stat.status = f"error: {e}"[:120]
            result.failed += 1
            logger.error(f"검색 실패 [{query.region.value}/{query.kind}] {query.q}: {e}")
        else:
            if query.kind == "health" and stat.raw == 0:
                health_zero.append(query.region.value)
            if stat.raw >= _STALE_CANARY_MIN_ITEMS and stat.stale * 2 > stat.raw:
                stale_queries.append(f"{query.region.value}/{query.kind}")
                logger.error(f"when: 무시 의심 ({stat.stale}/{stat.raw}건이 기간 밖) — q 길이 확인: {query.q}")
            logger.info(f"[{query.region.value}/{query.kind}] {stat.outlets}: {stat.raw}건 중 {stat.kept}건 통과")
        result.stats[-1] = stat.as_dict()

    if client.tripped:
        result.warnings.append(
            f"Google News 차단 감지({client.tripped}) — 검색 {result.failed}/{result.total}회를 건너뛰었습니다"
        )
    elif result.failed:
        result.warnings.append(f"Google News 검색 {result.failed}/{result.total}회 실패")
    if health_zero:
        result.warnings.append(f"Google News 점검 검색 결과 0건 ({', '.join(health_zero)}) — 응답 이상 가능성")
        logger.warning(f"점검 쿼리 0건: {health_zero}")
    if stale_queries:
        result.warnings.append(f"검색 기간 조건이 무시된 쿼리 {len(stale_queries)}개 ({', '.join(stale_queries)})")
    logger.info(f"Google News 검색 {result.total}회 (실패 {result.failed}) · 요청 {client.requests}회 · 통과 {len(result.items)}건")
    return result


# --- 원문 URL 복원 (선별된 기사만) ---

def tidy_publisher_url(url: str) -> str:
    """머니투데이 AMP 주소(/amp/...)를 일반 기사 주소로"""
    parts = urlsplit(url)
    if (parts.hostname or "").endswith("mt.co.kr") and parts.path.startswith("/amp/"):
        return url.replace("/amp/", "/", 1)
    return url


# 본 도메인에 보도자료를 싣는 매체 — 원문 경로(/press-release/)를 확인하지 못하면 보내지 않는다
UNVERIFIED_PATH_RISK_OUTLETS = frozenset({"usatoday"})


def is_non_article_path(url: str) -> bool:
    path = urlsplit(url).path or "/"
    return path.startswith(DROP_PATH_PREFIXES)


def resolve_urls(items: list[NewsItem], cache: dict[str, str], client: Optional[GoogleNewsClient] = None,
                 max_decodes: Optional[int] = None) -> tuple[list[NewsItem], list[str]]:
    """원문 URL을 채운다. 반환: (남길 기사, 경고). 기사 아닌 경로(회사 프로필 등)는 뺀다.

    cache: Google ID → 원문 URL (상태 파일에 저장). 차단되면 나머지는 Google 링크로 남긴다.
    """
    client = client or GoogleNewsClient()
    limit = get_config().max_decodes if max_decodes is None else max_decodes
    warnings: list[str] = []
    attempted = failed = 0
    kept: list[NewsItem] = []
    deferred = 0
    for item in items:
        url = cache.get(item.google_id)
        checked = url is not None
        if url is None and not client.tripped and attempted < limit:
            attempted += 1
            checked = True
            try:
                url = client.decode(item.google_url)
            except GoogleThrottled:
                url, checked = None, False
            if url:
                cache[item.google_id] = url
            else:
                failed += 1
        if url is None and not checked and item.outlet_key in UNVERIFIED_PATH_RISK_OUTLETS:
            # 차단·상한으로 원문 경로를 못 봤다 — 보도자료일 수 있어 보내지 않고 다음 실행으로 미룬다
            # (전송 이력·탈락 캐시에 남기지 않으므로 다음 실행에서 다시 판정·복원된다)
            deferred += 1
            logger.info(f"원문 경로 확인 불가 — 다음 실행으로 미룸: [{item.outlet_name}] {item.title}")
            continue
        if url:
            url = tidy_publisher_url(url)
            host = (urlsplit(url).hostname or "").lower()
            outlet = OUTLETS.get(item.outlet_key)
            if outlet is not None and host not in outlet.keep_hosts:
                logger.info(f"원문 호스트가 언론사 목록 밖 ({host}) — 제외: {item.title}")
                continue
            if is_non_article_path(url):
                logger.info(f"기사 아닌 경로 — 제외: {url}")
                continue
            if item.outlet_key == "forbes" and "/councils/" in url:
                item.outlet_name = "Forbes (Councils 기고)"
            item.url = url
        kept.append(item)
    if attempted or deferred:
        logger.info(f"원문 URL 복원: 시도 {attempted} · 실패 {failed} · 미룸 {deferred}")
    if attempted and failed * 2 > attempted:
        warnings.append(f"원문 URL 복원 실패 {failed}/{attempted}건 — 해당 기사는 Google News 링크로 보냅니다")
    if client.tripped and any(i.url is None for i in kept):
        warnings.append("Google News 차단으로 일부 기사는 Google News 링크로 보냅니다")
    return kept, warnings
