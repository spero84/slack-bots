"""AEC News 데이터 모델"""
import re
import unicodedata
from datetime import datetime
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field


class Region(str, Enum):
    """언론사 국가"""
    KR = "KR"
    US = "US"
    SG = "SG"


REGION_LABELS = {
    Region.KR: "🇰🇷 국내",
    Region.US: "🇺🇸 미국",
    Region.SG: "🇸🇬 싱가포르",
}


class Theme(str, Enum):
    """사용자가 지정한 키워드 주제 5개"""
    EPC_AI = "epc_ai"
    AEC_AI = "aec_ai"
    EPC_RAG = "epc_rag"
    AEC_RAG = "aec_rag"
    DRAWING_BIM = "drawing_bim"


THEME_LABELS = {
    Theme.EPC_AI: "EPC × AI",
    Theme.AEC_AI: "AEC × AI",
    Theme.EPC_RAG: "EPC × RAG",
    Theme.AEC_RAG: "AEC × RAG",
    Theme.DRAWING_BIM: "AI × 도면/BIM",
}


_TITLE_STRIP_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_title(title: str) -> str:
    """중복 비교용 제목 (NFKC, 소문자, 기호/공백 제거)"""
    text = unicodedata.normalize("NFKC", title or "").lower()
    return _TITLE_STRIP_RE.sub("", text)


def canonical_url(url: Optional[str]) -> str:
    """중복 비교용 URL (스킴/www/쿼리/프래그먼트/끝 슬래시 무시)

    쿼리는 기사 ID가 들어가는 언론사(예: `?idxno=`, `?no=`)가 있어 버리면 안 된다.
    추적용 파라미터(utm_*, ref, fbclid 등)만 제거한다.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    query = "&".join(
        q for q in parts.query.split("&")
        if q and not re.match(r"(?i)(utm_[a-z]+|ref|fbclid|gclid|cmpid|smid|taid|mod)=", q)
    )
    path = re.sub(r"/amp(?=/|$)", "", parts.path).rstrip("/") or "/"
    return urlunsplit(("", host, path, query, ""))


class NewsItem(BaseModel):
    """검색으로 찾은 기사 1건"""

    title: str = Field(description="기사 제목 (Google News의 ' - 언론사' 접미사 제거)")
    outlet_key: str = Field(description="언론사 키 (config.OUTLETS)")
    outlet_name: str = Field(description="언론사 표시 이름")
    region: Region
    published_at: datetime = Field(description="게시 시각 (timezone-aware)")
    google_id: str = Field(description="Google News 기사 ID (RSS guid)")
    google_url: str = Field(description="Google News 기사 링크 (원문으로 리다이렉트)")
    host: str = Field(description="Google News <source url>의 호스트")
    url: Optional[str] = Field(default=None, description="원문 URL (디코딩 성공 시)")
    snippet: str = ""
    query_themes: list[Theme] = Field(default_factory=list, description="이 기사를 찾은 검색 주제들")
    alias_ids: list[str] = Field(default_factory=list, description="같은 기사로 합친 다른 Google ID (AMP 사본 등)")

    # Bedrock 판정 결과
    score: Optional[float] = None
    theme: Optional[Theme] = None
    summary_ko: Optional[str] = None
    story: Optional[int] = Field(default=None, description="같은 소식 묶음 번호 (대표 기사 번호)")

    @property
    def link(self) -> str:
        return self.url or self.google_url

    @property
    def normalized_title(self) -> str:
        return normalize_title(self.title)

    @property
    def title_key(self) -> str:
        """언론사+정규화 제목 — Google News는 같은 기사에 ID를 둘 이상 주기도 해서(AMP 사본 등) 이것이 주 키다"""
        return f"title:{self.outlet_key}:{self.normalized_title}"

    def history_keys(self) -> list[str]:
        """전송 이력 키: 언론사+제목, Google ID, 원문 URL (어느 하나라도 같으면 이미 보낸 기사)"""
        keys = [self.title_key, f"gid:{self.google_id}", *(f"gid:{a}" for a in self.alias_ids)]
        if self.url:
            keys.append(f"url:{canonical_url(self.url)}")
        return keys
