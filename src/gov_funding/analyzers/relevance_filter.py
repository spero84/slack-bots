"""키워드 기반 관련성 필터링"""
import re
from datetime import datetime, timedelta

from ..storage import Announcement
from ..utils import EXCLUDE_KEYWORDS, RELEVANCE_KEYWORDS, ALLOWED_REGIONS, EXCLUDE_REGIONS, get_config, logger


def deadline_filter(announcements: list[Announcement]) -> list[Announcement]:
    """마감일 기반 필터링 - 2개월 이내 공고만 포함

    Bedrock 비용 절감을 위해 마감일이 너무 먼 공고를 제외.
    마감일 정보가 없는 경우 공고 게시일(posted_date)로 판단.
    둘 다 없는 경우 포함.

    Args:
        announcements: 공고 목록

    Returns:
        필터링된 공고 목록
    """
    config = get_config()
    max_days = config.deadline_max_days
    now = datetime.now()
    future_cutoff = now + timedelta(days=max_days)
    past_cutoff = now - timedelta(days=max_days)

    filtered = []
    excluded_past = 0
    excluded_too_far = 0
    excluded_old_posted = 0

    for ann in announcements:
        if ann.deadline is not None:
            if ann.deadline < now:
                excluded_past += 1
                logger.debug(f"제외 (마감): {ann.title} (마감일: {ann.deadline.strftime('%Y-%m-%d')})")
                continue
            if ann.deadline > future_cutoff:
                excluded_too_far += 1
                logger.debug(f"제외 (마감 {max_days}일 초과): {ann.title} (마감일: {ann.deadline.strftime('%Y-%m-%d')})")
                continue
        elif ann.posted_date is not None:
            if ann.posted_date < past_cutoff:
                excluded_old_posted += 1
                logger.debug(f"제외 (공고일 {max_days}일 초과): {ann.title} (공고일: {ann.posted_date.strftime('%Y-%m-%d')})")
                continue

        filtered.append(ann)

    logger.info(
        f"마감일 필터링: {len(announcements)}건 → {len(filtered)}건 "
        f"(마감 {excluded_past}건, {max_days}일 초과 {excluded_too_far}건, "
        f"공고일 초과 {excluded_old_posted}건 제외)"
    )
    return filtered


def keyword_filter(announcements: list[Announcement]) -> list[Announcement]:
    """키워드 기반 1차 필터링

    비용 절감을 위해 Bedrock 호출 전에 명확히 관련 없는 공고를 제외.

    Args:
        announcements: 공고 목록

    Returns:
        필터링된 공고 목록
    """
    filtered = []

    for ann in announcements:
        # 제목 + 카테고리 텍스트 결합
        text = f"{ann.title} {ann.category or ''}"

        # 지역 필터링 체크
        if not _is_allowed_region(ann):
            logger.debug(f"제외 (지역): {ann.title}")
            continue

        # 제외 키워드 체크
        if _contains_exclude_keyword(text):
            logger.debug(f"제외 (키워드): {ann.title}")
            continue

        # 관련 키워드 체크
        if _contains_relevance_keyword(text):
            filtered.append(ann)
        else:
            logger.debug(f"제외 (관련 없음): {ann.title}")

    logger.info(f"키워드 필터링: {len(announcements)}건 → {len(filtered)}건")
    return filtered


def _is_allowed_region(ann: Announcement) -> bool:
    """허용된 지역인지 확인

    제목, 카테고리, 소관부처에서 지역 정보를 추출하여 판단.
    지역 정보가 없으면 전국 대상으로 간주하여 허용.

    Args:
        ann: 공고

    Returns:
        허용 여부
    """
    # 제목에서 [지역] 패턴 추출
    text = f"{ann.title} {ann.category or ''} {ann.department or ''}"

    # 제외 지역이 포함되어 있으면 제외
    for region in EXCLUDE_REGIONS:
        if region in text:
            return False

    # 허용 지역이 포함되어 있으면 허용
    for region in ALLOWED_REGIONS:
        if region in text:
            return True

    # 지역 정보가 없으면 전국 대상으로 간주하여 허용
    # 제목에 [지역명] 패턴이 있는지 확인
    region_pattern = re.search(r'\[([가-힣]+)\]', ann.title)
    if region_pattern:
        # 지역 패턴이 있는데 허용 지역이 아니면 제외
        return False

    return True


def _contains_relevance_keyword(text: str) -> bool:
    """관련 키워드 포함 여부"""
    text_lower = text.lower()
    for keyword in RELEVANCE_KEYWORDS:
        # 대소문자 무시, 단어 경계 고려
        pattern = rf"\b{re.escape(keyword.lower())}\b"
        if re.search(pattern, text_lower):
            return True
        # 한글 키워드는 단어 경계 없이 검색
        if re.search(re.escape(keyword), text):
            return True
    return False


def _contains_exclude_keyword(text: str) -> bool:
    """제외 키워드 포함 여부"""
    for keyword in EXCLUDE_KEYWORDS:
        if keyword in text:
            return True
    return False


def calculate_keyword_score(text: str) -> float:
    """키워드 기반 점수 계산 (0-1)

    관련 키워드 개수에 따른 간단한 점수화.

    Args:
        text: 검사할 텍스트

    Returns:
        관련성 점수 (0-1)
    """
    text_lower = text.lower()
    matched = 0

    for keyword in RELEVANCE_KEYWORDS:
        if keyword.lower() in text_lower or keyword in text:
            matched += 1

    # 최대 점수 제한
    return min(matched / 5, 1.0)
