"""키워드 기반 관련성 필터링"""
import re

from ..storage import Announcement
from ..utils import EXCLUDE_KEYWORDS, RELEVANCE_KEYWORDS, ALLOWED_REGIONS, EXCLUDE_REGIONS, logger


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
