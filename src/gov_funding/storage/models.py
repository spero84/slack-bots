"""데이터 모델 정의"""
import re
import unicodedata
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field

# 가운뎃점 변형: 'ㆍ'(U+318D)와 NFKC 결과 'ᆞ'(U+119E)는 \w로 취급되어 일반 특수문자 제거로 지워지지 않는다
MIDDLE_DOTS = "·ㆍᆞ‧•･・∙⋅"
_MIDDLE_DOT_RE = re.compile(f"[{re.escape(MIDDLE_DOTS)}]")
_YEAR_DO_RE = re.compile(r"(\d{4})\s*년도")
# 제목 맨 앞의 상태 태그: (수정공고) [재공고] (공모) 등
_LEADING_STATUS_RE = re.compile(
    r"^\s*[\[(【〔]\s*(?:수정공고|재공고|정정공고|연장공고|변경공고|수정|정정|연장|변경|재공모|공모|공고)\s*[\])】〕]\s*"
)
_NON_WORD_RE = re.compile(r"[\W_]+")
# 제목 끝의 상투어 (반복 제거: '모집 공고 안내' → '', '모집 수정 공고' → '', '공고(수정)' → '')
# '추가모집'은 '추가'가 남으므로 원래 '모집' 공고와 같아지지 않는다 (다른 차수)
_TRAILING_BOILERPLATE_RE = re.compile(
    r"(?:공고문|모집공고|재공고|수정공고|정정공고|연장공고|변경공고|공고|안내|수정|정정|연장|변경|재공모|공모|모집)$"
)


def _prepare_title(title: str) -> str:
    """정규화 공통 전처리: NFKC, 소문자, 'YYYY년도'→'YYYY년', 맨 앞 상태 태그 제거 (가운뎃점은 남김)"""
    t = unicodedata.normalize("NFKC", title).lower()
    t = _YEAR_DO_RE.sub(r"\1년", t)
    while True:
        stripped = _LEADING_STATUS_RE.sub("", t, count=1)
        if stripped == t:
            break
        t = stripped
    return t


def strip_trailing_boilerplate(text: str) -> str:
    """끝의 '공고/모집공고/공고문/재공고/수정공고/안내/수정/연장/공모/모집' 등을 반복 제거 (전부 지워지면 원래 값 유지)"""
    while True:
        stripped = _TRAILING_BOILERPLATE_RE.sub("", text)
        if stripped == text or not stripped:
            return text
        text = stripped


def canon_title(title: Optional[str]) -> str:
    """중복 판정용 정규 제목

    같은 사업이 소스/게시 시점에 따라 다르게 표기되는 차이를 없앤다.
    - NFKC 정규화 후 소문자
    - 가운뎃점 변형(·ㆍ‧•･・∙) 제거
    - 'YYYY년도' → 'YYYY년'
    - 맨 앞 (수정공고)/(재공고)/(공모) 등 상태 태그 제거
    - 「」『』｢｣ 따옴표와 괄호/대괄호는 기호만 지우고 내용은 유지, 그 밖의 비단어 문자 제거
    - 끝의 공고문/모집공고/재공고/수정공고/공고/안내 (및 수정/정정/연장/변경/공모/모집) 반복 제거
      → '… 모집공고' / '… 모집 재공고' / '… 공고(수정)' / '… 공모'는 같은 값이 된다 (같은 사업의 단계 변경)

    예: '「2026 데이터+AI 혁신 챌린지」 통합 공고 안내' → '2026데이터ai혁신챌린지통합'

    Args:
        title: 공고 제목

    Returns:
        정규 제목 (빈 제목은 빈 문자열)
    """
    if not title:
        return ""
    t = _prepare_title(title)
    t = _MIDDLE_DOT_RE.sub("", t)
    t = _NON_WORD_RE.sub("", t)
    t = strip_trailing_boilerplate(t)
    return t or title.strip().lower()


def title_words(title: Optional[str]) -> list[str]:
    """유사도 비교용 단어 목록 (가운뎃점/기호를 공백으로 바꿔 분리, 단어 끝 상투어 제거)"""
    if not title:
        return []
    t = _prepare_title(title)
    t = _MIDDLE_DOT_RE.sub(" ", t)
    t = _NON_WORD_RE.sub(" ", t)
    words = []
    for word in t.split():
        word = strip_trailing_boilerplate(word)
        if word:
            words.append(word)
    return words


class Source(str, Enum):
    """공고 출처"""
    KSTARTUP = "kstartup"
    BIZINFO = "bizinfo"
    NIPA = "nipa"
    NIA = "nia"
    IITP = "iitp"
    JOINTIPS = "jointips"
    MSIT = "msit"
    MSS = "mss"
    MOTIE = "motie"


class ChangeType(str, Enum):
    """변경 유형"""
    NEW = "new"
    UPDATED = "updated"
    DEADLINE_SOON = "deadline_soon"
    CLOSED = "closed"


class Announcement(BaseModel):
    """지원사업 공고 모델"""

    id: str = Field(description="공고 고유 ID (pbancSn/pblancId)")
    source: Source = Field(description="출처 (kstartup/bizinfo)")
    title: str = Field(description="공고 제목")
    category: Optional[str] = Field(default=None, description="분야/카테고리")
    deadline: Optional[datetime] = Field(default=None, description="마감일")
    d_day: Optional[int] = Field(default=None, description="D-day")
    department: Optional[str] = Field(default=None, description="소관부처")
    organization: Optional[str] = Field(default=None, description="주관기관")
    url: str = Field(description="상세 페이지 URL")
    posted_date: Optional[datetime] = Field(default=None, description="공고 게시일")
    summary: Optional[str] = Field(default=None, description="AI 요약")
    relevance_score: Optional[float] = Field(default=None, description="관련성 점수 (0-1)")
    crawled_at: datetime = Field(default_factory=datetime.now, description="크롤링 시각")

    @computed_field
    @property
    def is_deadline_soon(self) -> bool:
        """마감 임박 여부 (7일 이내)"""
        if self.d_day is not None:
            return 0 < self.d_day <= 7
        return False

    @computed_field
    @property
    def normalized_title(self) -> str:
        """정규화된 제목 (중복 체크용) — canon_title()과 동일

        공백/특수문자/가운뎃점(·ㆍ) 제거, 소문자, 'YYYY년도'→'YYYY년', 상투어(공고/안내 등) 제거.
        """
        return canon_title(self.title)

    def __hash__(self):
        return hash(self.id + self.source.value)

    def __eq__(self, other):
        if isinstance(other, Announcement):
            return self.id == other.id and self.source == other.source
        return False


class AnnouncementChange(BaseModel):
    """공고 변경 사항"""

    announcement: Announcement
    change_type: ChangeType
    previous_data: Optional[dict] = Field(default=None, description="이전 데이터 (업데이트 시)")


class Snapshot(BaseModel):
    """스냅샷 모델"""

    source: Source
    timestamp: datetime = Field(default_factory=datetime.now)
    announcements: list[Announcement] = Field(default_factory=list)

    @computed_field
    @property
    def announcement_ids(self) -> set[str]:
        """공고 ID 집합"""
        return {a.id for a in self.announcements}


class NotificationPayload(BaseModel):
    """알림 페이로드"""

    new_announcements: list[Announcement] = Field(default_factory=list)
    deadline_soon: list[Announcement] = Field(default_factory=list)
    updated_announcements: list[AnnouncementChange] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def has_content(self) -> bool:
        """알림 내용이 있는지"""
        return bool(self.new_announcements or self.deadline_soon or self.updated_announcements)

    @computed_field
    @property
    def total_count(self) -> int:
        """총 알림 수"""
        return len(self.new_announcements) + len(self.deadline_soon) + len(self.updated_announcements)
