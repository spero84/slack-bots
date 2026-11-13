"""원문 기사 본문 확인 — 제목에 경쟁사 이름이 없는 기사(본문 언급)를 검증할 때만 쓴다

Google News RSS의 description은 제목과 출처뿐이라, 이름이 본문에만 있는 기사(포비콘·클라이원트처럼 협력사·데이터
출처로 언급되는 경우)는 제목만으로 맞는지 알 수 없다. 원문 페이지를 받아 이름 주변 문장을 발췌해 Bedrock에 넘긴다.
이름이 본문에 없으면(Google이 사이드바·관련 기사 문구로 매칭한 경우) 경쟁사 기사가 아니다.
"""
import html
import re
import time
from html.parser import HTMLParser
from typing import Callable, Optional

import requests

from .logger import logger

TIMEOUT = (5, 15)
MAX_BYTES = 3_000_000
TOTAL_SECONDS = 30      # 페이지 하나에 쓰는 최대 시간 (TIMEOUT은 소켓 읽기 한 번 기준)
EXCERPT_WIDTH = 160
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
_WS_RE = re.compile(r"\s+")

# 기사 본문 컨테이너 표시 (국내 CMS ndsoft 'article-view-content-div', 연합 'story-news', schema.org articleBody 등)
_BODY_MARK_RE = re.compile(
    r"articlebody|article-view-content|article_body|article-body|articlebody|news_body|newsbody|news-body|"
    r"story-news|article_txt|article-txt|art_body|view_con|article-content|article_content|entry-content|"
    r"post-content|story-body|caas-body|body-text|articletext",
    re.IGNORECASE,
)
# 본문 안에서도 건너뛸 블록 (관련·인기·추천 기사 목록, 광고, 공유 버튼, 기자 정보, 태그) — class/id 토큰 기준
# (부분 문자열로 보면 'content-stage'가 tag, 'canvas'가 nav에 걸린다)
_NOISE_PREFIXES = ("relat", "popular", "recommend", "rank", "best", "most", "aside", "sidebar", "banner", "advert",
                   "promo", "share", "sns", "social", "comment", "reporter", "byline", "copyright", "keyword",
                   "newsletter", "subscribe", "footer", "header", "nav", "menu", "photo", "caption", "figcaption",
                   "series", "more", "tag")
_NOISE_EXACT = {"ad", "ads", "adv", "ad_area"}
_TOKEN_SPLIT_RE = re.compile(r"[\s_\-:]+")


def _is_noise(marks: str) -> bool:
    for token in _TOKEN_SPLIT_RE.split(marks.lower()):
        if token and (token in _NOISE_EXACT or token.startswith(_NOISE_PREFIXES)):
            return True
    return False


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "figure",
              "figcaption", "button", "select", "template", "iframe"}
# 관련·인기 기사 목록의 제목 (짧은 텍스트 노드 전체가 이것일 때만 — 본문 문장 속 '관련 기사', 'more on'은 해당 없음).
# 이 제목을 담은 블록은 끝날 때까지 건너뛴다
_LIST_HEADING_RE = re.compile(
    r"(관련\s?기사|관련\s?뉴스|많이\s?본\s?(기사|뉴스)|인기\s?(기사|뉴스)|추천\s?기사|주요\s?뉴스|최신\s?기사|"
    r"이\s?시각\s?(주요|인기)\s?뉴스|함께\s?보면\s?좋은\s?기사|"
    r"Related (Articles|Stories|Coverage|News|Content)|Recommended( for You| Reading| Stories)?|"
    r"More (from|on) [A-Z][\w&.' -]{1,40}|Most (Read|Popular|Viewed)|Trending( Now)?|Read More|Top Stories)"
    r"[\s:：>»›]*"
)
_HEADING_MAX_CHARS = 40
# 봇 차단·자바스크립트 확인 페이지 (본문이 없는 것과 구별해 '받기 실패'로 본다 — 탈락 캐시에 넣지 않게)
_CHALLENGE_RE = re.compile(
    r"Incapsula incident|Request unsuccessful|Just a moment|Checking your browser|Enable JavaScript and cookies|"
    r"Attention Required|Access Denied|captcha|비정상적인 접근|자동입력 방지", re.IGNORECASE)


class _TextExtractor(HTMLParser):
    """텍스트 노드를 모은다.

    body 모드: 본문 컨테이너(_BODY_MARK_RE) 안만. page 모드: 페이지 전체.
    두 모드 모두 _SKIP_TAGS·잡음 블록과, 관련 기사 목록 제목(_LIST_HEADING_RE)을 담은 블록은 건너뛴다.
    """

    def __init__(self, body_mode: bool, strong_only: bool = False):
        super().__init__(convert_charrefs=True)
        self.body_mode = body_mode
        self.strong_only = strong_only   # itemprop=articleBody만 본문 컨테이너로 본다 (1차 시도)
        self.stack: list[str] = []  # 열린 태그 (짝이 안 맞는 HTML: 닫는 태그는 같은 이름까지 꺼내고, 없으면 무시)
        self.body_depth = None    # 지금 열려 있는 본문 컨테이너의 깊이 (body 모드)
        self.body_strong = False  # 그 컨테이너가 itemprop=articleBody인지
        self.skip_depth = None    # 건너뛰는 블록이 열린 깊이
        self.done = False         # 지금 컨테이너에서 더 모으지 않음 (목록 제목을 만남)
        self.parts: list[str] = []
        # 닫힌 본문 컨테이너들 [(articleBody 여부, 텍스트)] — 첫 컨테이너가 머리말 래퍼일 때가 있어 모두 모아 고른다
        self.candidates: list[tuple[bool, str]] = []

    @staticmethod
    def _marks(attrs) -> str:
        return " ".join(v for k, v in attrs if k in ("id", "class", "itemprop", "role") and v)

    @property
    def collecting(self) -> bool:
        if self.done or self.skip_depth is not None:
            return False
        return self.body_depth is not None if self.body_mode else True

    def _close_body(self):
        self.candidates.append((self.body_strong, " ".join(self.parts)))
        self.parts, self.body_depth, self.body_strong, self.done = [], None, False, False

    def best_body(self) -> Optional[str]:
        if self.body_depth is not None:      # 닫히지 않은 채 문서가 끝남
            self._close_body()
        if not self.candidates:
            return None
        strong = [t for flag, t in self.candidates if flag and t.strip()]
        pool = strong or [t for _, t in self.candidates]
        return max(pool, key=len)

    @property
    def depth(self) -> int:
        return len(self.stack)

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        self.stack.append(tag)
        marks = self._marks(attrs)
        if self.body_mode and self.body_depth is None:
            strong = any(k == "itemprop" and (v or "").lower() == "articlebody" for k, v in attrs)
            if strong or (not self.strong_only and _BODY_MARK_RE.search(marks) and not _is_noise(marks)):
                self.body_depth = self.depth
                self.body_strong = strong
            return
        if self.done:
            return
        if self.skip_depth is None and (tag in _SKIP_TAGS or (marks and _is_noise(marks))):
            self.skip_depth = self.depth

    def handle_startendtag(self, tag, attrs):
        """<br/>, <div/> 같은 자체 닫힘 태그는 깊이를 바꾸지 않는다"""
        return

    def handle_endtag(self, tag):
        if tag in _VOID or tag not in self.stack:
            return                       # 열린 적 없는 닫는 태그는 무시
        while self.stack:
            if self.stack.pop() == tag:
                break
        # 꺼낸 뒤 깊이가 블록이 열린 깊이보다 작아졌으면 그 블록이 닫힌 것
        if self.skip_depth is not None and self.depth < self.skip_depth:
            self.skip_depth = None
        if self.body_depth is not None and self.depth < self.body_depth:
            self._close_body()

    def handle_data(self, data):
        if not self.collecting:
            return
        stripped = data.strip()
        if stripped and len(stripped) <= _HEADING_MAX_CHARS and _LIST_HEADING_RE.fullmatch(stripped):
            # 목록 제목: 제목을 담은 블록(부모)이 끝날 때까지 건너뛴다. 부모가 본문 컨테이너 자체면 거기서 끝
            parent = max(1, self.depth - 1)
            if self.body_depth is not None and parent <= self.body_depth:
                self.done = True
            else:
                self.skip_depth = parent
            return
        self.parts.append(data)


def _plain(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def _extract(page: str, body_mode: bool, strong_only: bool = False) -> tuple[bool, str]:
    parser = _TextExtractor(body_mode, strong_only)
    try:
        parser.feed(page)
        parser.close()
    except Exception:  # noqa: BLE001 — 깨진 HTML: 모은 데까지만
        pass
    if body_mode:
        body = parser.best_body()
        return body is not None, _plain(body or "")
    return True, _plain(" ".join(parser.parts))


def html_to_text(page: str) -> str:
    """기사 본문 텍스트

    1) itemprop=articleBody 요소가 있으면 그 안만 (schema.org 표준, 국내 ndsoft CMS도 쓴다)
    2) 없으면 본문 표시 class/id(article-view-content, story-news, article-body …) 컨테이너 중 가장 긴 것
    3) 그것도 없으면 페이지 전체 (메뉴·헤더·푸터·사이드바 태그 제외)
    두 경우 모두 관련·인기 기사 목록, 광고, 공유, 기자 정보 블록은 뺀다 (사이드바 제목의 회사명 오탐 방지).
    """
    page = page or ""
    for strong_only in (True, False):     # itemprop=articleBody → 본문 표시 class/id → 페이지 전체
        found, text = _extract(page, body_mode=True, strong_only=strong_only)
        if found and text:
            return text
    _, text = _extract(page, body_mode=False)
    return text


def is_challenge_page(text: str) -> bool:
    """봇 차단·확인 페이지(본문 대신 안내문만 온 경우)"""
    return len(text) < 600 and bool(_CHALLENGE_RE.search(text))


def mention_excerpt(text: str, pattern: re.Pattern, width: int = EXCERPT_WIDTH) -> Optional[str]:
    """이름이 처음 나오는 곳 앞뒤 width자"""
    match = pattern.search(text or "")
    if not match:
        return None
    start = max(0, match.start() - width)
    end = min(len(text), match.end() + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


def decode_page(raw: bytes, header_encoding: Optional[str]) -> str:
    """HTTP 헤더 → <meta charset> → UTF-8 순으로 인코딩을 정한다 (국내 일부 매체는 EUC-KR)"""
    candidates = []
    if header_encoding and header_encoding.lower() != "iso-8859-1":   # requests 기본값(헤더에 charset 없음)은 믿지 않는다
        candidates.append(header_encoding)
    meta = _META_CHARSET_RE.search(raw[:8192])
    if meta:
        candidates.append(meta.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for enc in candidates:
        if enc.lower().replace("_", "-") in ("euc-kr", "ks-c-5601-1987", "ksc5601"):
            enc = "cp949"   # EUC-KR 선언 페이지도 확장 한글(CP949)을 쓴다 — euc-kr로 엄격히 읽으면 실패한다
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


class ArticleFetcher:
    """원문 페이지를 순서대로 받는다 (요청 간격 1초)"""

    def __init__(self, session: Optional[requests.Session] = None, interval: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.interval = interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._fetched = 0

    def fetch_text(self, url: str) -> Optional[str]:
        """기사 텍스트. 실패(차단·타임아웃·HTML 아님·봇 확인 페이지)는 None — 이 함수는 예외를 올리지 않는다"""
        if self._fetched and self.interval > 0:
            self._sleep(self.interval)
        self._fetched += 1
        try:
            with self.session.get(url, timeout=TIMEOUT, stream=True) as resp:
                if resp.status_code != 200:
                    logger.info(f"원문 받기 HTTP {resp.status_code}: {url}")
                    return None
                ctype = resp.headers.get("Content-Type", "")
                if "html" not in ctype and "xml" not in ctype:
                    return None
                # iter_content는 urllib3 오류(끊김·잘못된 gzip)를 requests 예외로 바꿔 준다. 느리게 흘리는 서버는 총 시간으로 끊는다
                deadline = self._monotonic() + TOTAL_SECONDS
                chunks, size = [], 0
                for chunk in resp.iter_content(65536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BYTES or self._monotonic() > deadline:
                        break
                text = html_to_text(decode_page(b"".join(chunks), resp.encoding))
        except Exception as e:  # noqa: BLE001 — 원문 하나 때문에 실행 전체가 멈추면 안 된다
            logger.info(f"원문 받기 실패 ({e.__class__.__name__}): {url}")
            return None
        if is_challenge_page(text):
            logger.info(f"원문 받기 실패 (봇 확인 페이지): {url}")
            return None
        return text
