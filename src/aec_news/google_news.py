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
import http.cookiejar
import json
import math
import os
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
    COMPETITOR_BLOCK_HOSTS,
    COMPETITOR_QUERIES,
    COMPETITORS,
    DROP_PATH_PREFIXES,
    DROP_PATH_SEGMENTS,
    EDITIONS,
    HEALTH_QUERIES,
    MAX_QUERY_CHARS,
    OUTLETS,
    SEARCH_KIND_THEME,
    SEARCH_TERMS,
    WEB_QUERIES,
    Competitor,
    Outlet,
    get_config,
    outlet_takes_kind,
)
from .logger import logger
from .models import KOREAN_REGIONS, NewsItem, Region

SEARCH_URL = "https://news.google.com/rss/search"
ARTICLE_URL = "https://news.google.com/rss/articles/{aid}"   # /read/ 경로는 실측 중 429(sorry)를 받았다
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
TIMEOUT = (5, 20)
RETRY_BACKOFF = (10, 30)
# 브라우저와 같은 일관된 헤더 (한 실행 동안 바꾸지 않는다 — 요청마다 바뀌는 헤더가 오히려 자동화로 보인다)
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
}
# 지역판별 Accept-Language (한국어판은 한국어 우선, 영어판은 영어 우선 — 사람이 그 판을 볼 때의 브라우저 설정)
EDITION_LANGUAGE = {
    "ko": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US": "en-US,en;q=0.9,ko;q=0.6",
    "en-GB": "en-GB,en;q=0.9,en-US;q=0.8,ko;q=0.6",
    "en-IN": "en-IN,en;q=0.9,en-US;q=0.8,ko;q=0.6",
    "en-SG": "en-SG,en;q=0.9,en-US;q=0.8,ko;q=0.6",
}
_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="(\d+)"')
_DECODE_REQ = (
    '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,'
    'null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts},"{sg}"]'
)
_HANGUL_RE = re.compile(r"[가-힣]")
_BAD_TITLE_RE = re.compile(r"^(untitled|속보서비스|.{0,3})$", re.IGNORECASE)
_STALE_CANARY_MIN_ITEMS = 10
# 190자 제한 뒤로 진짜 잘림은 일어나지 않는다. 잘린 쿼리는 결과 대부분(실측 64/67, 71/77)이 기간 밖이었고,
# 본문 매칭 쿼리(RAG)는 몇 건 안 되는 결과 중 옛 기사가 섞이는 일이 흔하다 (2026-11-13 4/7건 오탐)
_STALE_CANARY_RATIO = 0.7


class GoogleThrottled(RuntimeError):
    """429/503/sorry/동의 페이지 — 이번 실행의 Google 호출을 모두 멈춘다"""


# --- 쿼리 만들기 ---

@dataclass(frozen=True)
class SearchQuery:
    kind: str                    # drawing / aec_ai / rag / health / competitor
    region: Region               # 언론사 섹션 (경쟁사 검색은 지역판 기준 KR 또는 GLOBAL)
    q: str
    outlet_keys: tuple[str, ...]  # 경쟁사 검색은 (경쟁사 키,)
    days: int
    edition: str = "en-US"       # config.EDITIONS 키
    open: bool = False           # 매체 무관 검색 (site: 없음 — 목록 밖 매체는 🔎 기타 매체 섹션으로)

    @property
    def url(self) -> str:
        ed = EDITIONS[self.edition]
        return f"{SEARCH_URL}?{urlencode({'q': self.q, 'hl': ed.hl, 'gl': ed.gl, 'ceid': ed.ceid})}"


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
    """주제 × 섹션 × 지역판 × 언론사 묶음 쿼리 + 섹션별 점검 쿼리

    site: 묶음은 같은 지역판(edition)을 쓰는 언론사끼리만 만든다 (BBC는 en-GB, Times of India는 en-IN 등).
    """
    queries: list[SearchQuery] = []
    for region, days in days_by_region.items():
        outlets = [o for o in OUTLETS.values() if o.region == region]
        if not outlets:
            continue
        by_edition: dict[str, list[Outlet]] = {}
        for outlet in outlets:
            by_edition.setdefault(outlet.search_edition, []).append(outlet)
        for kind, terms_by_region in SEARCH_TERMS.items():
            terms = terms_by_region.get(region)
            if not terms:
                continue
            for edition, members in by_edition.items():
                members = [o for o in members if outlet_takes_kind(o, kind)]
                if not members:
                    continue
                for group in pack_outlets(terms, members, days):
                    queries.append(SearchQuery(kind, region, _compose(terms, group, days),
                                               tuple(o.key for o in group), days, edition))
    for region, terms, keys in HEALTH_QUERIES:
        if region not in days_by_region:
            continue
        group = [OUTLETS[k] for k in keys if k in OUTLETS]
        if not group:
            continue
        editions = {o.search_edition for o in group}
        if len(editions) != 1:
            raise ValueError(f"점검 쿼리 언론사는 지역판이 같아야 한다: {keys}")
        queries.append(SearchQuery("health", region, _compose(terms, group, 1),
                                   tuple(o.key for o in group), 1, editions.pop()))
    return queries


def build_web_queries(days_override: Optional[int] = None, specs=WEB_QUERIES) -> list[SearchQuery]:
    """매체 무관 검색 — `{검색어} when:Nd` (site: 없음)"""
    queries: list[SearchQuery] = []
    for terms, edition, days, kind in specs:
        days = days_override or days
        q = f"{terms} when:{days}d"
        if len(q) > MAX_QUERY_CHARS:
            raise ValueError(f"매체 무관 검색어가 너무 길다 ({len(q)}자): {q}")
        region = Region.WEB_KR if edition == "ko" else Region.WEB_GLOBAL
        queries.append(SearchQuery(kind, region, q, (), days, edition, open=True))
    return queries


def build_competitor_queries(days: int, specs=COMPETITOR_QUERIES) -> list[SearchQuery]:
    """경쟁사 검색 — 언론사 제한 없이 `{검색어} when:Nd`. outlet_keys = 후보 회사 키"""
    queries: list[SearchQuery] = []
    for terms, edition, keys in specs:
        q = f"{terms} when:{days}d"
        if len(q) > MAX_QUERY_CHARS:
            raise ValueError(f"경쟁사 검색어가 너무 길다 ({len(q)}자): {q}")
        region = Region.KR if edition == "ko" else Region.GLOBAL
        queries.append(SearchQuery("competitor", region, q, tuple(keys), days, edition))
    return queries


# --- HTTP 클라이언트 (간격 제한 + 차단 감지) ---

class GoogleNewsClient:
    """news.google.com 요청을 사람이 브라우저로 보는 속도로 한 줄로 세운다 (사용자 요청 2026-11-13)

    - 요청 간격: 매번 무작위 gap_min~gap_max초 (기본 4~9초). 일정한 간격은 자동화로 보인다
    - 12~20회마다 25~60초 '읽는 시간' 휴식
    - 기사 링크를 열면(원문 URL 복원) 페이지 GET 뒤 0.6~1.6초 있다가 이어지는 POST (브라우저 스크립트가 도는 시간)
    - 같은 브라우저 헤더 + 지역판별 Accept-Language, Google 쿠키를 실행 사이에 유지 (돌아온 브라우저처럼)
    - 429/503/google.com/sorry(캡차)/동의 페이지를 받으면 그 실행의 Google 호출을 모두 멈춘다 (재시도하지 않는다).
      캡차를 풀거나 IP를 바꾸는 식으로 우회하지 않는다
    interval=0이면 대기 없이 (테스트용).
    """

    def __init__(self, session: Optional[requests.Session] = None, interval: Optional[float] = None,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
                 rng: Optional[random.Random] = None, cookie_path: Optional[str] = None):
        config = get_config()
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.human = interval is None or interval > 0
        self.gap = (config.gap_min, config.gap_max) if interval is None else (interval, interval + 1.0)
        self.break_every = config.break_every
        self.break_secs = config.break_secs
        self.rng = rng or random.Random()
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: Optional[float] = None
        self._since_break = 0
        self._next_break = self.rng.randint(*self.break_every)
        self.tripped: Optional[str] = None     # 차단 사유 (있으면 이후 호출 전부 거부)
        self.requests = 0
        self.cookie_path = cookie_path
        if cookie_path and session is None:
            self._load_cookies()

    # --- 쿠키 (실행 사이 유지) ---

    def _load_cookies(self):
        jar = http.cookiejar.LWPCookieJar(self.cookie_path)
        try:
            jar.load(ignore_discard=True, ignore_expires=False)
        except (OSError, http.cookiejar.LoadError):
            pass
        self.session.cookies = jar

    def save_cookies(self):
        jar = self.session.cookies
        if not self.cookie_path or not isinstance(jar, http.cookiejar.LWPCookieJar):
            return
        try:
            os.makedirs(os.path.dirname(self.cookie_path), exist_ok=True)
            jar.save(ignore_discard=True, ignore_expires=False)
        except OSError as e:
            logger.warning(f"Google 쿠키 저장 실패: {e}")

    def _wait(self):
        if not self.human:
            self._last = self._monotonic()
            return
        if self._last is not None:
            gap = self.rng.uniform(*self.gap)
            self._since_break += 1
            if self._since_break >= self._next_break:
                rest = self.rng.uniform(*self.break_secs)
                logger.info(f"Google News: {self._since_break}회 요청 뒤 {rest:.0f}초 쉼")
                gap += rest
                self._since_break = 0
                self._next_break = self.rng.randint(*self.break_every)
            remaining = self._last + gap - self._monotonic()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._monotonic()

    def pause(self, low: float, high: float):
        """짧은 사람 같은 멈춤 (기사 페이지를 연 뒤 등)"""
        if self.human:
            self._sleep(self.rng.uniform(low, high))

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

    def request(self, method: str, url: str, *, paced: bool = True, edition: Optional[str] = None,
                **kwargs) -> requests.Response:
        """네트워크 오류·5xx(503 제외)는 10초, 30초 뒤 두 번 재시도. 차단 신호는 재시도하지 않는다."""
        if self.tripped:
            raise GoogleThrottled(self.tripped)
        if edition in EDITION_LANGUAGE:
            kwargs["headers"] = {**kwargs.get("headers", {}), "Accept-Language": EDITION_LANGUAGE[edition]}
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
            # 서명은 이 GET의 ts에 묶여 있어 곧바로 POST한다 — 브라우저에서 페이지 스크립트가 도는 만큼만 (0.6~1.6초)
            self.pause(0.6, 1.6)
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

def match_outlet(host: str, region: Optional[Region]) -> Optional[Outlet]:
    """<source url> 호스트 → 언론사. keep_hosts에 정확히 있는 호스트만 (하위 도메인·영문판은 None).
    region이 None이면 모든 섹션에서 찾는다 (경쟁사 검색)."""
    host = (host or "").lower()
    for outlet in OUTLETS.values():
        if (region is None or outlet.region == region) and host in outlet.keep_hosts:
            return outlet
    return None


def is_outlet_noise_host(host: str) -> bool:
    """목록 언론사 도메인의 하위 도메인인데 keep_hosts가 아닌 곳 (시세·포럼·광고·영문판 사본)"""
    host = (host or "").lower()
    for outlet in OUTLETS.values():
        if host == outlet.domain or host.endswith("." + outlet.domain):
            return host not in outlet.keep_hosts
    return False


def is_blocked_competitor_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == b or host.endswith("." + b) for b in COMPETITOR_BLOCK_HOSTS)


_PATTERN_CACHE: dict[tuple, Optional[re.Pattern]] = {}


def _alias_regex(parts: tuple[str, ...]) -> Optional[re.Pattern]:
    """영문 이름은 영문·숫자 경계(한글 조사가 붙어도 매칭 — '올거나이즈의', 'BHSN과'), 대소문자 무시"""
    if not parts:
        return None
    key = parts
    if key not in _PATTERN_CACHE:
        _PATTERN_CACHE[key] = re.compile(
            "|".join(rf"(?<![A-Za-z0-9])(?:{p})(?![A-Za-z0-9])" for p in parts), re.IGNORECASE)
    return _PATTERN_CACHE[key]


def tag_pattern(comp: Competitor) -> Optional[re.Pattern]:
    """주제 검색 기사 제목을 훑을 때 쓰는 고유 이름 정규식 (없으면 None — 그 회사는 경쟁사 검색으로만 찾는다)"""
    return _alias_regex(comp.tag_aliases)


def names_pattern(comp: Competitor) -> Optional[re.Pattern]:
    """기사에 나오는 이름 표기 전체(리터럴) — 제목·본문에 회사명이 실제로 있는지 볼 때"""
    return _alias_regex(tuple(re.escape(n) for n in comp.names))


def tag_competitors(items: list[NewsItem], competitors: tuple[Competitor, ...] = COMPETITORS):
    """제목·스니펫에 경쟁사의 고유 이름이 있으면 competitor_hits에 추가 (주제 검색으로 찾은 기사도 경쟁사 확인을 거친다)"""
    for item in items:
        text = f"{item.title} {item.snippet}"
        for comp in competitors:
            pat = tag_pattern(comp)
            if pat is not None and comp.key not in item.competitor_hits and pat.search(text):
                item.competitor_hits.append(comp.key)


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
        competitor_search = query.kind == "competitor"
        source_title = (source.get("title") or "").strip()
        if competitor_search or query.open:
            # 경쟁사·매체 무관 검색은 언론사 목록 밖도 받는다. 목록 언론사의 잡음 하위 도메인·차단 출처만 뺀다.
            # 목록 언론사 기사는 그 언론사 섹션으로 (매체 무관 검색이 site: 검색과 같은 기사를 찾으면 한 건으로 합쳐진다)
            outlet = match_outlet(host, None)
            if outlet is None and (not host or is_outlet_noise_host(host) or is_blocked_competitor_host(host)):
                continue
            region = outlet.region if outlet else query.region
            outlet_key = outlet.key if outlet else f"web:{host}"
            outlet_name = outlet.name_for(host) if outlet else (source_title or host)
        else:
            outlet = match_outlet(host, query.region)
            if outlet is None:
                continue
            region, outlet_key, outlet_name = query.region, outlet.key, outlet.name_for(host)
        if published is None or not (window_start <= published <= now + timedelta(hours=1)):
            continue
        title = clean_title(entry.get("title", ""), source_title)
        if _BAD_TITLE_RE.match(title):
            continue
        if not competitor_search:
            has_hangul = bool(_HANGUL_RE.search(title))
            if (query.region in KOREAN_REGIONS) != has_hangul:
                continue   # 국내 검색의 영어 제목(영문판) / 해외 검색의 한글 제목은 버린다
        google_id = entry.get("id") or article_id(entry.get("link", "")) or entry.get("link", "")
        items.append(NewsItem(
            title=title,
            outlet_key=outlet_key,
            outlet_name=outlet_name,
            region=region,
            published_at=published,
            google_id=google_id,
            google_url=entry.get("link", ""),
            host=host,
            snippet=_snippet(entry.get("description", ""), title, source_title),
            query_themes=[SEARCH_KIND_THEME[query.kind]] if query.kind in SEARCH_KIND_THEME else [],
            competitor_hits=list(query.outlet_keys) if competitor_search else [],
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
    base = config.kr_lookback_days if region in KOREAN_REGIONS else config.intl_lookback_days
    if last_success is not None:
        gap_days = math.ceil(max(0.0, (now - last_success).total_seconds()) / 86400) + 1
        base = max(base, gap_days)
    return max(1, min(config.max_lookback_days, base))


def search_all(now: datetime, days_by_region: dict[Region, int], hours: Optional[float] = None,
               client: Optional[GoogleNewsClient] = None, competitor_days: Optional[int] = None,
               web: bool = False) -> SearchResult:
    """모든 쿼리를 순서대로 실행. 쿼리 하나의 실패는 건너뛰고, 차단이면 나머지를 모두 건너뛴다.

    hours를 주면 모든 국가의 게시 시각 필터를 now - hours로 맞춘다 (when:은 올림한 일수).
    competitor_days를 주면 경쟁사 검색도 한다. 끝에 모든 기사 제목에서 경쟁사 이름을 찾아 표시한다.
    """
    client = client or GoogleNewsClient()
    queries = build_queries(days_by_region)
    if web:
        queries += build_web_queries(max(1, math.ceil(hours / 24)) if hours is not None else None)
    if competitor_days is not None:
        queries += build_competitor_queries(competitor_days)
    if client.human:
        client.rng.shuffle(queries)   # 매일 같은 순서로 훑지 않는다 (결과 합치기는 순서와 무관 — dedupe_run이 합친다)
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
            resp = client.request("GET", query.url, edition=query.edition)
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
            if query.open and stat.raw >= 100:
                # 관련도순 100건 한도 — 일부 기사가 밀려났을 수 있다 (쿼리를 나누거나 기간을 줄일 때)
                logger.warning(f"매체 무관 검색 결과가 100건 한도에 닿음: {query.q}")
            if stat.raw >= _STALE_CANARY_MIN_ITEMS and stat.stale > stat.raw * _STALE_CANARY_RATIO:
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
    tag_competitors(result.items)
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
    if path.startswith(DROP_PATH_PREFIXES):
        return True
    return any(seg.lower() in DROP_PATH_SEGMENTS for seg in path.split("/"))


def resolve_urls(items: list[NewsItem], cache: dict[str, str], client: Optional[GoogleNewsClient] = None,
                 max_decodes: Optional[int] = None, competitor_mode: bool = False) -> tuple[list[NewsItem], list[str]]:
    """원문 URL을 채운다. 반환: (남길 기사, 경고). 기사 아닌 경로(회사 프로필 등)는 뺀다.

    cache: Google ID → 원문 URL (상태 파일에 저장). 차단되면 나머지는 Google 링크로 남긴다.
    competitor_mode(또는 item.competitor): 경쟁사 기사 — 보도자료 경로도 받는다 (투자·출시 소식이 보도자료로 나온다).
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
        is_comp = competitor_mode or bool(item.competitor)
        if url is None and not checked and item.outlet_key in UNVERIFIED_PATH_RISK_OUTLETS and not is_comp:
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
            if outlet is not None and outlet.keep_paths and not (urlsplit(url).path or "/").startswith(outlet.keep_paths):
                logger.info(f"원문 경로가 {outlet.name} 밖 — 제외: {url}")   # bizjournals.com의 다른 도시판
                continue
            if is_non_article_path(url) and not (is_comp and "/press-release" in url):
                logger.info(f"기사 아닌 경로 — 제외: {url}")
                continue
            if item.outlet_key == "forbes" and "/councils/" in url and "기고" not in item.outlet_name:
                item.outlet_name = "Forbes (Councils 기고)"
            if "/spons/" in url and "스폰서" not in item.outlet_name:
                item.outlet_name = f"{item.outlet_name} (스폰서 콘텐츠)"   # Construction Dive 등: 회사가 쓴 광고 기사
            item.url = url
        kept.append(item)
    if attempted or deferred:
        logger.info(f"원문 URL 복원: 시도 {attempted} · 실패 {failed} · 미룸 {deferred}")
    if attempted and failed * 2 > attempted:
        warnings.append(f"원문 URL 복원 실패 {failed}/{attempted}건 — 해당 기사는 Google News 링크로 보냅니다")
    if client.tripped and any(i.url is None for i in kept):
        warnings.append("Google News 차단으로 일부 기사는 Google News 링크로 보냅니다")
    return kept, warnings
