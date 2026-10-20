"""S3 스냅샷 저장소 및 공고 중복 제거"""
import json
import re
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Iterable, NamedTuple, Optional

import boto3
from botocore.exceptions import ClientError

from ..utils import get_config, logger
from ..utils.dates import today_kst
from .models import (
    Announcement,
    AnnouncementChange,
    ChangeType,
    NotificationPayload,
    Snapshot,
    Source,
    canon_title,
    title_words,
)

# 유사 제목 병합 기준 (다른 소스 사이에서만 적용)
# ai-news의 0.6은 여기서는 위험하다: '2차 vs 3차', '정보보호 vs 로봇' 같은 다른 사업이 0.76~0.97로 나온다
FUZZY_RATIO_THRESHOLD = 0.90
# 유사 비교에 쓰는 정규 제목의 최소 길이 (너무 짧으면 한두 글자 차이도 0.9를 넘는다)
FUZZY_MIN_LENGTH = 8
# 상투어를 뺀 핵심 문자열의 최소 길이
FUZZY_MIN_CORE_LENGTH = 4
# 포함 검사/유사도 계산에서 무시하는 상투어 (모집 공고에 흔한 일반 단어)
FUZZY_STOPWORDS = frozenset({
    "공고", "공고문", "모집", "모집공고", "안내", "사업", "사업공고", "프로젝트",
    "수정", "수정공고", "재공고", "신규", "지원", "및", "의", "통합", "공모", "년", "년도",
    "과제", "신규과제", "정정", "정정공고", "연장", "연장공고", "변경", "변경공고", "재공모",
    "참여", "참가", "기업", "참여기업", "참가기업", "기업모집", "참여자", "참가자",
    "신청", "접수", "대상",
})
# 복합어 앞뒤에서 떼어낼 상투어 ('지원사업' → '', '수요기업' → '수요'), 긴 것부터
_AFFIX_STOPWORDS = tuple(sorted((w for w in FUZZY_STOPWORDS if len(w) >= 2), key=len, reverse=True))
_DIGITS_RE = re.compile(r"\d+")
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_YEAR_WORD_RE = re.compile(r"(?:19|20)\d{2}년?")


class S3Storage:
    """S3 기반 스냅샷 저장소"""

    def __init__(self, bucket: Optional[str] = None, region: Optional[str] = None):
        config = get_config()
        self.bucket = bucket or config.s3_bucket
        self.region = region or config.aws_region
        self.s3_client = boto3.client("s3", region_name=self.region)

    def _get_latest_key(self, source: Source) -> Optional[str]:
        """최신 스냅샷 키 조회"""
        prefix = f"snapshots/{source.value}/"
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=1000,
            )
            if "Contents" not in response:
                return None

            # 가장 최근 파일 반환
            objects = sorted(response["Contents"], key=lambda x: x["LastModified"], reverse=True)
            return objects[0]["Key"] if objects else None
        except ClientError as e:
            logger.error(f"S3 목록 조회 실패: {e}")
            return None

    def get_latest_snapshot(self, source: Source) -> Optional[Snapshot]:
        """최신 스냅샷 조회"""
        key = self._get_latest_key(source)
        if not key:
            logger.info(f"{source.value} 스냅샷 없음")
            return None

        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            data = json.loads(response["Body"].read().decode("utf-8"))
            return Snapshot(**data)
        except ClientError as e:
            logger.error(f"S3 스냅샷 조회 실패: {e}")
            return None
        except Exception as e:
            logger.error(f"스냅샷 파싱 실패: {e}")
            return None

    def save_snapshot(self, snapshot: Snapshot) -> bool:
        """스냅샷 저장"""
        timestamp = snapshot.timestamp.strftime("%Y%m%d_%H%M%S")
        key = f"snapshots/{snapshot.source.value}/{timestamp}.json"

        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=snapshot.model_dump_json(indent=2),
                ContentType="application/json",
            )
            logger.info(f"스냅샷 저장 완료: {key}")
            return True
        except ClientError as e:
            logger.error(f"S3 스냅샷 저장 실패: {e}")
            return False

    def compare_snapshots(
        self,
        current: list[Announcement],
        source: Source,
        deadline_alert_days: int = 7,
    ) -> tuple[Snapshot, NotificationPayload]:
        """스냅샷 비교 및 변경사항 추출

        Args:
            current: 현재 크롤링한 공고 목록
            source: 출처
            deadline_alert_days: 마감 임박 기준일

        Returns:
            (새 스냅샷, 알림 페이로드)
        """
        # 새 스냅샷 생성
        new_snapshot = Snapshot(
            source=source,
            timestamp=datetime.now(),
            announcements=current,
        )

        # 이전 스냅샷 조회
        previous = self.get_latest_snapshot(source)

        payload = NotificationPayload()

        if previous is None:
            # 첫 실행: 모든 공고가 신규
            payload.new_announcements = current
            logger.info(f"첫 실행 - 신규 공고 {len(current)}건")
        else:
            # 이전 공고 ID 매핑
            prev_by_id = {a.id: a for a in previous.announcements}
            prev_ids = set(prev_by_id.keys())
            curr_ids = {a.id for a in current}

            # 신규 공고
            new_ids = curr_ids - prev_ids
            for ann in current:
                if ann.id in new_ids:
                    payload.new_announcements.append(ann)

            # 마감 임박 공고 (기존 공고 중 D-day가 임박한 것)
            for ann in current:
                if ann.id not in new_ids and ann.d_day is not None:
                    if 0 < ann.d_day <= deadline_alert_days:
                        # 이전에 이미 마감 임박이었는지 확인
                        prev_ann = prev_by_id.get(ann.id)
                        if prev_ann and prev_ann.d_day is not None:
                            # 이전에도 임박이었으면 스킵 (중복 알림 방지)
                            if prev_ann.d_day <= deadline_alert_days:
                                continue
                        payload.deadline_soon.append(ann)

            logger.info(
                f"스냅샷 비교 완료 - 신규: {len(payload.new_announcements)}건, "
                f"마감임박: {len(payload.deadline_soon)}건"
            )

        return new_snapshot, payload


def _strip_stopword_affixes(word: str) -> str:
    """복합어 앞뒤의 상투어 제거 ('신규지원' → '', '클린제조환경조성사업' → '클린제조환경조성', '수요기업' → '수요')"""
    changed = True
    while changed:
        changed = False
        for stop in _AFFIX_STOPWORDS:
            if len(word) > len(stop) and word.endswith(stop):
                word = word[:-len(stop)]
                changed = True
                break
            if len(word) > len(stop) and word.startswith(stop):
                word = word[len(stop):]
                changed = True
                break
    return word


class _TitleKey:
    """중복 비교용으로 미리 계산한 제목 정보"""

    __slots__ = ("canon", "compact", "digits", "words", "core", "core_sorted", "bucket")

    def __init__(self, title: str):
        self.canon = canon_title(title)
        words = title_words(title)
        # 상투어 제거 전 전체 문자열 (포함 검사용: canon은 끝의 '모집공고'를 지워 '추가모집'이 안 보인다)
        self.compact = "".join(words)
        self.digits = Counter(d for w in words for d in _DIGITS_RE.findall(w))
        # 포함 검사 대상 단어: 상투어와 연도('2026', '2026년')는 제외
        self.words = [w for w in words if w not in FUZZY_STOPWORDS and not _YEAR_WORD_RE.fullmatch(w)]
        # 유사도 계산용 핵심 문자열: 상투어(복합어 앞뒤 포함)와 연도를 뺀 단어 (원래 순서 / 정렬 순서)
        core_words = [w for w in (_strip_stopword_affixes(w) for w in self.words) if w and w not in FUZZY_STOPWORDS]
        self.core = "".join(core_words)
        self.core_sorted = "".join(sorted(core_words))
        # 연도를 뺀 숫자 묶음 — 숫자 규칙상 이 값이 다르면 절대 병합되지 않으므로 비교 후보를 줄이는 데 쓴다
        self.bucket = tuple(sorted(d for d in self.digits.elements() if not _YEAR_RE.fullmatch(d)))


def _digits_compatible(a: Counter, b: Counter) -> bool:
    """숫자 토큰이 같은지 (한쪽에만 있는 4자리 연도 1개는 무시)

    '2차' vs '3차', '5G' vs '6G', '2026' vs '2027'은 다르고, '2026년도 AX실증밸리' vs 'AX실증밸리'는 같다.
    """
    if a == b:
        return True
    only_a, only_b = a - b, b - a
    if only_a and only_b:
        return False
    diff = only_a or only_b
    if sum(diff.values()) != 1:
        return False
    return _YEAR_RE.fullmatch(next(iter(diff))) is not None


def _fuzzy_ratio(a: _TitleKey, b: _TitleKey) -> Optional[float]:
    """보수적 유사 규칙을 모두 만족하면 유사도, 아니면 None

    1) 숫자 토큰 일치 (한쪽에만 있는 연도 1개는 무시)
    2) 양쪽 제목 각각의 상투어가 아닌 단어가 모두 상대 제목에 포함 (띄어쓰기/어순/상투어 차이만 허용)
       짧은 쪽만 검사하면 '… 수요기업 모집' vs '… 수요기업 추가 모집', '창업기업' vs '추천형 창업기업'처럼
       한쪽에만 있는 수식어로 갈리는 다른 공고가 병합된다 (실데이터 4쌍).
    3) 상투어·연도를 뺀 핵심 문자열의 SequenceMatcher 유사도 >= FUZZY_RATIO_THRESHOLD
       (원래 어순과 단어 정렬 순서 중 높은 값 — '2차 정보보호…' vs '정보보호… 2차')
       예: '서초창업스테이션 프리-오픈이노베이션' vs '서초창업스테이션 프리 오픈 이노베이션 참여 기업 모집 공고'
    """
    if min(len(a.canon), len(b.canon)) < FUZZY_MIN_LENGTH:
        return None
    la, lb = len(a.core), len(b.core)
    if min(la, lb) < FUZZY_MIN_CORE_LENGTH:
        return None
    # ratio의 상한(2*min/(la+lb))으로 먼저 거른다
    if 2 * min(la, lb) / (la + lb) < FUZZY_RATIO_THRESHOLD:
        return None
    if not _digits_compatible(a.digits, b.digits):
        return None
    if not all(word in b.compact for word in a.words) or not all(word in a.compact for word in b.words):
        return None
    best = 0.0
    for x, y in ((a.core, b.core), (a.core_sorted, b.core_sorted)):
        matcher = SequenceMatcher(None, x, y, autojunk=False)
        if matcher.quick_ratio() < FUZZY_RATIO_THRESHOLD:
            continue
        best = max(best, matcher.ratio())
        if best >= 1.0:
            break
    return best if best >= FUZZY_RATIO_THRESHOLD else None


def is_similar_title(title_a: str, title_b: str) -> bool:
    """두 제목이 같은 사업 공고로 보이는지 (정규 제목 일치 또는 보수적 유사 규칙)

    소스 구분 없이 제목만 비교한다. 같은 소스끼리 유사 규칙을 적용할지는 호출하는 쪽이 정한다.
    """
    a, b = _TitleKey(title_a), _TitleKey(title_b)
    if a.canon and a.canon == b.canon:
        return True
    return _fuzzy_ratio(a, b) is not None


class TitleMatch(NamedTuple):
    """TitleMatcher 매칭 결과"""
    payload: Any
    title: str  # 매칭된 저장 제목
    ratio: float  # 정확 일치는 1.0
    exact: bool  # 정규 제목 정확 일치 여부


class TitleMatcher:
    """저장된 제목 목록에서 같은 사업 공고를 찾는다 (is_similar_title과 같은 규칙)

    (제목, payload) 목록으로 한 번 만들고, 오늘 공고 제목마다 find()로 조회한다.
    - 정규 제목(canon_title) 정확 일치를 먼저 본다. 같은 정규 제목이 여러 개면 먼저 넣은 항목을 돌려준다
      (원하는 우선순위가 있으면 넣기 전에 정렬할 것).
    - 없으면 유사 규칙(_fuzzy_ratio)을 만족하는 후보 중 유사도가 가장 높은 항목을 돌려준다.
      후보는 연도를 뺀 숫자 묶음이 같고, 핵심 문자열 길이가 유사도 상한을 넘을 수 있는 범위만 본다.
    - 소스는 구분하지 않는다 (같은 소스가 새 ID로 다시 올린 공고도 같은 사업으로 본다).

    예:
        matcher = TitleMatcher((meta["title"], meta) for meta in index.values())
        meta = matcher.find(ann.title)  # 없으면 None
    """

    def __init__(self, items: Iterable[tuple[str, Any]]):
        self._exact: dict[str, tuple[str, Any]] = {}
        groups: dict[tuple, list[tuple[int, _TitleKey, str, Any]]] = defaultdict(list)
        size = 0
        for title, payload in items:
            if not title:
                continue
            key = _TitleKey(title)
            if not key.canon:
                continue
            size += 1
            self._exact.setdefault(key.canon, (title, payload))
            groups[key.bucket].append((len(key.core), key, title, payload))
        # 숫자 묶음별로 핵심 문자열 길이 순 정렬 (같은 길이는 넣은 순서 유지)
        self._buckets: dict[tuple, tuple[list[int], list[tuple[_TitleKey, str, Any]]]] = {}
        for bucket, entries in groups.items():
            entries.sort(key=lambda entry: entry[0])
            self._buckets[bucket] = ([e[0] for e in entries], [(e[1], e[2], e[3]) for e in entries])
        self._size = size

    def __len__(self) -> int:
        return self._size

    def match(self, title: Optional[str]) -> Optional[TitleMatch]:
        """가장 잘 맞는 저장 항목 (정확 일치 > 유사도 높은 순), 없으면 None"""
        key = _TitleKey(title or "")
        if not key.canon:
            return None
        exact = self._exact.get(key.canon)
        if exact is not None:
            return TitleMatch(exact[1], exact[0], 1.0, True)

        bucket = self._buckets.get(key.bucket)
        length = len(key.core)
        if bucket is None or length < FUZZY_MIN_CORE_LENGTH:
            return None
        lengths, entries = bucket
        # 2*min(n, m)/(n + m) >= t  ⇔  n*t/(2-t) <= m <= n*(2-t)/t
        t = FUZZY_RATIO_THRESHOLD
        lo = bisect_left(lengths, length * t / (2 - t) - 1e-9)
        hi = bisect_right(lengths, length * (2 - t) / t + 1e-9)
        best: Optional[TitleMatch] = None
        for candidate, candidate_title, payload in entries[lo:hi]:
            ratio = _fuzzy_ratio(candidate, key)
            if ratio is not None and (best is None or ratio > best.ratio):
                best = TitleMatch(payload, candidate_title, ratio, False)
                if ratio >= 1.0:
                    break
        return best

    def find(self, title: Optional[str]) -> Optional[Any]:
        """같은 사업으로 보이는 저장 항목의 payload (없으면 None)"""
        found = self.match(title)
        return found.payload if found is not None else None


def _is_expired(ann: Announcement, today: date) -> bool:
    """마감일이 오늘(KST) 이전인지"""
    return ann.deadline is not None and ann.deadline.date() < today


def _merge_duplicate(
    kept_list: list[Announcement],
    kept_keys: list[_TitleKey],
    pos: int,
    candidate: Announcement,
    candidate_key: _TitleKey,
    today: date,
    label: str,
) -> None:
    """중복 공고 병합: 기본은 먼저 들어온(우선순위 높은) 공고를 유지

    단, 유지 중인 공고는 이미 마감됐고 새 공고는 아직 유효하면 새 공고로 바꾼다.
    중복 제거가 마감일 필터보다 먼저 실행되므로, 그대로 두면 사업 자체가 사라진다
    (예: 마감된 원공고와 진행 중인 재공고).
    """
    kept = kept_list[pos]
    if _is_expired(kept, today) and not _is_expired(candidate, today):
        kept_list[pos] = candidate
        kept_keys[pos] = candidate_key
        kept, dropped, note = candidate, kept, ", 마감 지난 항목 대체"
    else:
        dropped, note = candidate, ""

    message = (
        f"중복 제거 ({label}{note}): [{dropped.source.value}] {dropped.title} ({dropped.url}) "
        f"→ 유지 [{kept.source.value}] {kept.title}"
    )
    if dropped.source == kept.source and dropped.id == kept.id:
        logger.debug(message)  # 같은 공고가 목록에 두 번 나온 경우 (페이지 경계, 고정 공지)
    else:
        logger.info(message)


def deduplicate_announcements(
    *announcement_lists: list[Announcement],
) -> list[Announcement]:
    """여러 출처의 공고를 병합하고 중복 제거

    인자 순서가 우선순위 (먼저 전달된 출처가 높은 우선순위).

    1차: canon_title() 정확 일치 (같은 소스 안에서도 적용)
    2차: 다른 소스 사이에서만 보수적 유사 규칙 (_fuzzy_ratio) — 같은 소스끼리는 유사 규칙으로 병합하지 않는다
         ('2026년도 2차 …' vs '2026년 3차 …' 같은 회차 차이는 숫자 규칙으로 병합하지 않는다)

    병합 시 우선순위가 높은 공고를 유지하되, 그 공고가 이미 마감됐고 다른 쪽이 유효하면 유효한 쪽을 남긴다.
    병합 내역은 INFO로 남긴다 (삭제된 공고의 URL 포함).

    Args:
        *announcement_lists: 우선순위 순서대로 전달된 공고 목록들

    Returns:
        중복 제거된 공고 목록
    """
    today = today_kst()

    # 1차: 정규 제목 정확 일치
    exact: list[Announcement] = []
    exact_keys: list[_TitleKey] = []
    position_by_canon: dict[str, int] = {}
    exact_merged = 0
    for announcements in announcement_lists:
        for ann in announcements:
            key = _TitleKey(ann.title)
            pos = position_by_canon.get(key.canon) if key.canon else None
            if pos is None:
                if key.canon:
                    position_by_canon[key.canon] = len(exact)
                exact.append(ann)
                exact_keys.append(key)
                continue
            exact_merged += 1
            _merge_duplicate(exact, exact_keys, pos, ann, key, today, "동일 제목")

    # 2차: 다른 소스 사이의 유사 제목
    result: list[Announcement] = []
    result_keys: list[_TitleKey] = []
    positions_by_bucket: dict[tuple, list[int]] = defaultdict(list)
    fuzzy_merged = 0
    for ann, key in zip(exact, exact_keys):
        best_pos, best_ratio = None, 0.0
        for pos in positions_by_bucket[key.bucket]:
            if result[pos].source == ann.source:
                continue
            ratio = _fuzzy_ratio(result_keys[pos], key)
            if ratio is not None and ratio > best_ratio:
                best_pos, best_ratio = pos, ratio
        if best_pos is None:
            positions_by_bucket[key.bucket].append(len(result))
            result.append(ann)
            result_keys.append(key)
            continue
        fuzzy_merged += 1
        _merge_duplicate(result, result_keys, best_pos, ann, key, today, f"유사 제목 {best_ratio:.2f}")

    # 소스별 건수 로그
    source_counts = []
    for announcements in announcement_lists:
        if announcements:
            source_name = announcements[0].source.value
            source_counts.append(f"{source_name}: {len(announcements)}건")
    counts_str = ", ".join(source_counts) if source_counts else "없음"
    logger.info(
        f"중복 제거 완료 - {counts_str} → 총: {len(result)}건 "
        f"(동일 제목 {exact_merged}건, 유사 제목 {fuzzy_merged}건 병합)"
    )
    return result
