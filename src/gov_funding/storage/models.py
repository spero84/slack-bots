"""데이터 모델 정의"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class Source(str, Enum):
    """공고 출처"""
    KSTARTUP = "kstartup"
    BIZINFO = "bizinfo"
    NIPA = "nipa"


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
        """정규화된 제목 (중복 체크용)"""
        # 공백, 특수문자 제거 후 소문자로 변환
        import re
        return re.sub(r"[^\w가-힣]", "", self.title.lower())

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
