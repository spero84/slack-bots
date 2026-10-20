"""키워드/지역/마감일 기반 1차 필터링

Bedrock 호출 전에 명확히 관련 없는 공고만 제외한다.
애매하면 통과시키고 최종 판단은 Bedrock이 한다 (1차 필터는 느슨하게 유지).
"""
import re
import unicodedata
from collections import Counter
from datetime import timedelta
from functools import lru_cache
from typing import Iterable, NamedTuple, Optional

from ..storage import Announcement
from ..utils import get_config, logger
from ..utils.config import (
    ALLOWED_REGIONS,
    ASCII_BOTH_BOUNDARY_KEYWORDS,
    EXCLUDE_KEYWORDS,
    EXCLUDE_REGIONS,
    KEYWORD_PATTERNS,
    NON_ACTIONABLE_PATTERNS,
    REGION_ALIAS_PATTERNS,
    REGION_AMBIGUOUS_PATTERNS,
    REGION_EXEMPT_CATEGORY_PATTERN,
    REGION_TAG_ONLY_ALIASES,
    RELEVANCE_KEYWORDS,
    SOFT_EXCLUDE_KEYWORDS,
    STRONG_IT_KEYWORDS,
)
from ..utils.dates import now_kst


def deadline_filter(announcements: list[Announcement]) -> list[Announcement]:
    """마감일 기반 필터링 - 2개월 이내 공고만 포함 (KST 날짜 기준)

    Bedrock 비용 절감을 위해 마감일이 너무 먼 공고를 제외.
    - 마감일이 오늘(KST)보다 이전이면 제외. 오늘 마감(D-0)은 유지한다.
      (시각까지 비교하면 K-Startup처럼 크롤링 시각으로 마감일을 만든 D-0 공고가 빠진다)
    - 마감일이 오늘 + deadline_max_days일보다 뒤면 제외.
    - 마감일 정보가 없는 경우 공고 게시일(posted_date)이 오늘 - deadline_max_days일보다 이전이면 제외.
    - 둘 다 없는 경우 포함.

    Args:
        announcements: 공고 목록

    Returns:
        필터링된 공고 목록
    """
    config = get_config()
    max_days = config.deadline_max_days
    # today_kst()와 같다. 모듈 전역 now_kst를 거쳐야 테스트에서 시각을 고정할 수 있다
    today = now_kst().date()
    future_cutoff = today + timedelta(days=max_days)
    past_cutoff = today - timedelta(days=max_days)

    filtered = []
    excluded_past = 0
    excluded_too_far = 0
    excluded_old_posted = 0

    for ann in announcements:
        if ann.deadline is not None:
            deadline_date = ann.deadline.date()
            if deadline_date < today:
                excluded_past += 1
                logger.debug(f"제외 (마감): {ann.title} (마감일: {deadline_date:%Y-%m-%d})")
                continue
            if deadline_date > future_cutoff:
                excluded_too_far += 1
                logger.debug(f"제외 (마감 {max_days}일 초과): {ann.title} (마감일: {deadline_date:%Y-%m-%d})")
                continue
        elif ann.posted_date is not None:
            posted = ann.posted_date.date()
            if posted < past_cutoff:
                excluded_old_posted += 1
                logger.debug(f"제외 (공고일 {max_days}일 초과): {ann.title} (공고일: {posted:%Y-%m-%d})")
                continue

        filtered.append(ann)

    logger.info(
        f"마감일 필터링: {len(announcements)}건 → {len(filtered)}건 "
        f"(마감 {excluded_past}건, {max_days}일 초과 {excluded_too_far}건, "
        f"공고일 초과 {excluded_old_posted}건 제외)"
    )
    return filtered


# 제외 사유 (keyword_filter 로그 순서)
REASON_REGION = "지역"
REASON_NON_ACTIONABLE = "결과·예고·마감"
REASON_EXCLUDE = "제외 키워드"
REASON_SOFT_EXCLUDE = "제외 키워드(약)"
REASON_NO_KEYWORD = "관련 키워드 없음"
_REASON_ORDER = [REASON_REGION, REASON_NON_ACTIONABLE, REASON_EXCLUDE, REASON_SOFT_EXCLUDE, REASON_NO_KEYWORD]


class KeywordVerdict(NamedTuple):
    """키워드 필터 판정"""
    passed: bool
    reason: Optional[str]  # 제외 사유 (통과 시 None)
    detail: str  # 매칭된 키워드/지역/패턴
    it_override: bool = False  # 제외 키워드가 있었지만 강한 IT 신호로 무시했는지


def keyword_filter(announcements: list[Announcement]) -> list[Announcement]:
    """키워드 기반 1차 필터링

    비용 절감을 위해 Bedrock 호출 전에 명확히 관련 없는 공고를 제외.
    순서: 지역 → 결과/예고/마감 표시(신청 불가) → 제외 키워드(강/약, 제목에 강한 IT 신호가 있으면 무시) → 관련 키워드.
    사유별 제외 건수를 INFO로 남긴다.

    Args:
        announcements: 공고 목록

    Returns:
        필터링된 공고 목록
    """
    filtered = []
    excluded = Counter()
    it_overrides = 0

    for ann in announcements:
        verdict = classify_keywords(ann)
        if verdict.it_override:
            it_overrides += 1
        if verdict.passed:
            filtered.append(ann)
            if verdict.it_override:
                logger.debug(f"통과 (IT 신호로 제외 키워드 무시: {verdict.detail}): {ann.title}")
            continue
        excluded[verdict.reason] += 1
        logger.debug(f"제외 ({verdict.reason}: {verdict.detail}): {ann.title}")

    breakdown = ", ".join(f"{reason} {excluded[reason]}건" for reason in _REASON_ORDER)
    logger.info(
        f"키워드 필터링: {len(announcements)}건 → {len(filtered)}건 "
        f"(제외: {breakdown} / IT 신호로 제외 키워드 무시 {it_overrides}건)"
    )
    return filtered


def classify_keywords(ann: Announcement) -> KeywordVerdict:
    """공고 1건의 키워드 필터 판정 (지역 포함)"""
    region = classify_region(ann.title, ann.category, ann.department)
    if region.verdict == REGION_EXCLUDE:
        return KeywordVerdict(False, REASON_REGION, region.reason)

    title = _normalize(ann.title)
    text = f"{title} {_normalize(ann.category)}"

    pattern = _find_pattern(text, NON_ACTIONABLE_PATTERNS)
    if pattern:
        return KeywordVerdict(False, REASON_NON_ACTIONABLE, pattern)

    # 제외 키워드는 제목+분야, 강한 IT 신호는 제목에서만 찾는다
    exclude_hit = _find_keyword(text, EXCLUDE_KEYWORDS)
    soft_hit = None if exclude_hit else _find_keyword(text, SOFT_EXCLUDE_KEYWORDS)
    it_override = False
    if exclude_hit or soft_hit:
        if _find_keyword(title, STRONG_IT_KEYWORDS) is None:
            if exclude_hit:
                return KeywordVerdict(False, REASON_EXCLUDE, exclude_hit)
            return KeywordVerdict(False, REASON_SOFT_EXCLUDE, soft_hit)
        it_override = True

    relevance_hit = _find_keyword(text, RELEVANCE_KEYWORDS)
    if relevance_hit is None:
        return KeywordVerdict(False, REASON_NO_KEYWORD, "", it_override)
    return KeywordVerdict(True, None, exclude_hit or soft_hit or relevance_hit, it_override)


# ---------------------------------------------------------------------------
# 지역 판정
# ---------------------------------------------------------------------------

REGION_ALLOW = "allow"
REGION_EXCLUDE = "exclude"
REGION_NATIONWIDE = "nationwide"


class RegionVerdict(NamedTuple):
    """지역 판정 결과"""
    verdict: str  # allow | exclude | nationwide
    reason: str  # 판정 근거 (소관부처/태그/매칭된 지역명)


# 광역자치단체명 (서울특별시, 경기도, 부산광역시, 세종특별자치시, 강원특별자치도, 전남광주통합특별시 ...)
_PROVINCE_DEPT_RE = re.compile(r"(?:특별시|광역시|특별자치시|특별자치도|통합특별시|도)$")
# 중앙부처/기관 (위 패턴과 겹치지 않지만 명시적으로 걸러낸다)
_CENTRAL_DEPT_SUFFIXES = ("부", "처", "청", "원", "위원회")
_ALLOWED_DEPT_PREFIXES = ("서울", "경기")
# 제목 맨 앞의 [태그] / (태그) / 【태그】 — 「」『』는 사업명 인용이라 태그로 보지 않는다
_LEAD_TAG_RE = re.compile(r"\s*[\[(【〔]\s*([^\[\]()【】〔〕]*?)\s*[\])】〕]")
_MAX_LEAD_TAGS = 3
# '경기 광주', '경기도 광주시'는 경기도 광주시 (광주광역시와 구분)
_GYEONGGI_GWANGJU_RE = re.compile(r"경기(?:도)?\s*광주(?:시)?")


def classify_region(
    title: Optional[str],
    category: Optional[str] = None,
    department: Optional[str] = None,
) -> RegionVerdict:
    """공고 대상 지역 판정 (회사 소재지: 서울/경기 성남)

    규칙 (먼저 걸리는 규칙으로 판정):
    1) 소관부처가 광역자치단체(…특별시/광역시/특별자치시/특별자치도/통합특별시/도)면
       서울특별시/경기도 → 허용, 그 외 → 제외 (기업마당 구조화 정보)
       분야가 조달/입찰(입찰공고, 조달입찰공고, 사전규격공개)이면 여기서 전국 대상으로 판정한다.
       제목의 지역명은 지원 자격이 아니라 사업 수행 장소이기 때문 (REGION_EXEMPT_CATEGORY_PATTERN).
    2) 제목 맨 앞 [태그]/(태그)는 지역명을 포함할 때만 지역으로 본다.
       허용 지역 포함 → 허용, 제외 지역 포함 → 제외. [공고], [GBSA], [코리아스타트업포럼]처럼
       지역명이 없는 태그는 무시하고 다음 태그/규칙으로 넘어간다.
    3) '경기 광주(시)'는 '경기'로 바꿔 본다.
    4) 제외 지역명(광역/도 정식·약칭, 권역, 대덕특구, 주요 시·군)이 어디든 있으면 제외.
       '대전환', '부산물', '전주기', '세종대왕', '아산나눔재단' 같은 오탐은 REGION_ALIAS_PATTERNS로 거른다.
    5) 단독 '광주', '동남권', '동북권'은 허용 지역 표시가 없을 때만 제외.
    6) 허용 지역명이 있으면 허용, 아무 지역 정보도 없으면 전국 대상(허용).

    Args:
        title: 공고 제목
        category: 분야/카테고리
        department: 소관부처

    Returns:
        RegionVerdict (verdict: allow/exclude/nationwide)
    """
    title_n = _normalize(title)
    dept = _normalize(department).strip()

    # 1) 광역자치단체 소관부처
    if dept and _PROVINCE_DEPT_RE.search(dept) and not dept.endswith(_CENTRAL_DEPT_SUFFIXES):
        if dept.startswith(_ALLOWED_DEPT_PREFIXES):
            return RegionVerdict(REGION_ALLOW, f"소관부처 {dept}")
        return RegionVerdict(REGION_EXCLUDE, f"소관부처 {dept}")

    # 조달/입찰 공고: 제목의 지역명은 수행 장소라 판정하지 않는다
    category_n = _normalize(category)
    if category_n and _compiled(REGION_EXEMPT_CATEGORY_PATTERN).search(category_n):
        return RegionVerdict(REGION_NATIONWIDE, f"조달/입찰 ({category_n})")

    # 2) 맨 앞 태그 (연속된 태그 최대 3개: '[재공고][부산] …')
    pos = 0
    for _ in range(_MAX_LEAD_TAGS):
        match = _LEAD_TAG_RE.match(title_n, pos)
        if not match:
            break
        tag = match.group(1)
        if _find_region(tag, ALLOWED_REGIONS):
            return RegionVerdict(REGION_ALLOW, f"태그 [{tag}]")
        if (
            _find_region(tag, EXCLUDE_REGIONS)
            or _find_keyword(tag, REGION_TAG_ONLY_ALIASES)
            or _find_pattern(tag, REGION_AMBIGUOUS_PATTERNS)
        ):
            return RegionVerdict(REGION_EXCLUDE, f"태그 [{tag}]")
        pos = match.end()

    # 3) 경기 광주(시) → 경기
    text = " ".join(part for part in (title_n, category_n, dept) if part)
    text = _GYEONGGI_GWANGJU_RE.sub("경기", text)

    # 4) 제외 지역명
    excluded = _find_region(text, EXCLUDE_REGIONS)
    if excluded:
        return RegionVerdict(REGION_EXCLUDE, excluded)

    # 5) 모호한 지역명 (허용 지역 표시가 없을 때만)
    allowed = _find_region(text, ALLOWED_REGIONS)
    if not allowed:
        ambiguous = _find_pattern(text, REGION_AMBIGUOUS_PATTERNS)
        if ambiguous:
            return RegionVerdict(REGION_EXCLUDE, ambiguous)

    # 6) 허용 지역 / 전국
    if allowed:
        return RegionVerdict(REGION_ALLOW, allowed)
    return RegionVerdict(REGION_NATIONWIDE, "")


def _is_allowed_region(ann: Announcement) -> bool:
    """허용된 지역인지 확인 (전국 대상 포함)

    Args:
        ann: 공고

    Returns:
        허용 여부 (classify_region이 exclude가 아니면 True)
    """
    return classify_region(ann.title, ann.category, ann.department).verdict != REGION_EXCLUDE


# ---------------------------------------------------------------------------
# 키워드 매칭
# ---------------------------------------------------------------------------

def _normalize(text: Optional[str]) -> str:
    """매칭용 텍스트 정규화 (NFKC: 전각 영문/괄호 → 반각)"""
    return unicodedata.normalize("NFKC", text) if text else ""


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern)


@lru_cache(maxsize=None)
def _keyword_pattern(keyword: str) -> Optional[re.Pattern]:
    """키워드 정규식 (None이면 부분 문자열 매칭)

    한글 키워드: KEYWORD_PATTERNS에 오탐 방지 정규식이 있으면 사용, 없으면 부분 문자열.
    ASCII 키워드:
    - 대소문자 구분
    - 앞 글자가 영문자(숫자로 시작하는 키워드는 숫자)면 매칭하지 않음: AICT/AIoT/AX-Ready는 허용, RETAIL/TRAINING은 거부
    - ASCII_BOTH_BOUNDARY_KEYWORDS(IT, SW)는 뒤 글자도 영문자가 아니어야 함: UNIT/ITEM/SUMMIT/SWISS/SWITCH 거부
    """
    if keyword in KEYWORD_PATTERNS:
        return re.compile(KEYWORD_PATTERNS[keyword])
    if not keyword.isascii():
        return None
    prefix = r"(?<![0-9])" if keyword[0].isdigit() else r"(?<![A-Za-z])"
    suffix = r"(?![A-Za-z])" if keyword in ASCII_BOTH_BOUNDARY_KEYWORDS else ""
    return re.compile(prefix + re.escape(keyword) + suffix)


def _keyword_in(text: str, keyword: str) -> bool:
    pattern = _keyword_pattern(keyword)
    if pattern is None:
        return keyword in text
    return pattern.search(text) is not None


def _find_keyword(text: str, keywords: Iterable[str]) -> Optional[str]:
    """처음 매칭되는 키워드 (없으면 None)"""
    return next((keyword for keyword in keywords if _keyword_in(text, keyword)), None)


def _find_pattern(text: str, patterns: Iterable[str]) -> Optional[str]:
    """처음 매칭되는 정규식의 매칭 문자열 (없으면 None)"""
    for pattern in patterns:
        match = _compiled(pattern).search(text)
        if match:
            return match.group(0)
    return None


def _find_region(text: str, aliases: Iterable[str]) -> Optional[str]:
    """처음 매칭되는 지역명 (REGION_ALIAS_PATTERNS의 오탐 방지 정규식 적용)"""
    for alias in aliases:
        if _compiled(REGION_ALIAS_PATTERNS.get(alias) or re.escape(alias)).search(text):
            return alias
    return None


def _contains_relevance_keyword(text: str) -> bool:
    """관련 키워드 포함 여부"""
    return _find_keyword(_normalize(text), RELEVANCE_KEYWORDS) is not None


def _contains_exclude_keyword(text: str) -> bool:
    """제외 키워드(강/약) 포함 여부 (IT 신호 예외는 적용하지 않은 원시 판정)"""
    text = _normalize(text)
    return _find_keyword(text, EXCLUDE_KEYWORDS) is not None or _find_keyword(text, SOFT_EXCLUDE_KEYWORDS) is not None


def calculate_keyword_score(text: str) -> float:
    """키워드 기반 점수 계산 (0-1)

    관련 키워드 개수에 따른 간단한 점수화.

    Args:
        text: 검사할 텍스트

    Returns:
        관련성 점수 (0-1)
    """
    text = _normalize(text)
    matched = sum(1 for keyword in RELEVANCE_KEYWORDS if _keyword_in(text, keyword))
    # 최대 점수 제한
    return min(matched / 5, 1.0)
