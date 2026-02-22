"""AI News 데이터 모델 정의"""
import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field


class ArticleSource(str, Enum):
    """기사 출처"""
    ARXIV = "arxiv"
    HACKERNEWS = "hackernews"
    TECHCRUNCH = "techcrunch"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    DEEPMIND = "deepmind"
    HUGGINGFACE = "huggingface"
    AITIMES = "aitimes"
    ITWORLD = "itworld"
    ETNEWS = "etnews"
    ITDAILY = "itdaily"
    AWS_BLOG = "aws_blog"
    AZURE_BLOG = "azure_blog"
    GOOGLE_BLOG = "google_blog"
    MS_RESEARCH = "ms_research"
    GOOGLE_RESEARCH = "google_research"
    MEDIUM = "medium"


class ArticleCategory(str, Enum):
    """기사 카테고리"""
    PAPER = "paper"
    COMPANY_NEWS = "company"
    INDUSTRY_NEWS = "industry"


# 소스별 기본 카테고리 매핑
SOURCE_CATEGORY_MAP = {
    ArticleSource.ARXIV: ArticleCategory.PAPER,
    ArticleSource.HUGGINGFACE: ArticleCategory.PAPER,
    ArticleSource.ANTHROPIC: ArticleCategory.COMPANY_NEWS,
    ArticleSource.OPENAI: ArticleCategory.COMPANY_NEWS,
    ArticleSource.DEEPMIND: ArticleCategory.COMPANY_NEWS,
    ArticleSource.TECHCRUNCH: ArticleCategory.INDUSTRY_NEWS,
    ArticleSource.HACKERNEWS: ArticleCategory.INDUSTRY_NEWS,
    ArticleSource.AITIMES: ArticleCategory.INDUSTRY_NEWS,
    ArticleSource.ITWORLD: ArticleCategory.INDUSTRY_NEWS,
    ArticleSource.ETNEWS: ArticleCategory.INDUSTRY_NEWS,
    ArticleSource.ITDAILY: ArticleCategory.INDUSTRY_NEWS,
    ArticleSource.AWS_BLOG: ArticleCategory.COMPANY_NEWS,
    ArticleSource.AZURE_BLOG: ArticleCategory.COMPANY_NEWS,
    ArticleSource.GOOGLE_BLOG: ArticleCategory.COMPANY_NEWS,
    ArticleSource.MS_RESEARCH: ArticleCategory.PAPER,
    ArticleSource.GOOGLE_RESEARCH: ArticleCategory.PAPER,
    ArticleSource.MEDIUM: ArticleCategory.INDUSTRY_NEWS,
}


class Article(BaseModel):
    """AI 뉴스 기사/논문 모델"""

    id: str = Field(description="소스별 고유 ID")
    source: ArticleSource = Field(description="출처")
    category: ArticleCategory = Field(description="카테고리")
    title: str = Field(description="제목")
    url: str = Field(description="원본 URL")
    authors: Optional[list[str]] = Field(default=None, description="저자 목록")
    published_at: Optional[datetime] = Field(default=None, description="게시일")
    summary: Optional[str] = Field(default=None, description="원본 요약/초록")
    ai_summary: Optional[str] = Field(default=None, description="Bedrock 생성 한국어 요약")
    importance_score: Optional[float] = Field(default=None, description="중요도 점수 (0-1)")
    tags: list[str] = Field(default_factory=list, description="태그 목록")
    extra: Optional[dict] = Field(default=None, description="소스별 추가 데이터 (HN score 등)")
    crawled_at: datetime = Field(default_factory=datetime.now, description="크롤링 시각")

    @computed_field
    @property
    def normalized_title(self) -> str:
        """정규화된 제목 (중복 체크용)"""
        return re.sub(r"[^\w]", "", self.title.lower())

    @computed_field
    @property
    def vector_key(self) -> str:
        """S3 Vectors 키"""
        return f"{self.source.value}_{self.id}"

    def __hash__(self):
        return hash(self.vector_key)

    def __eq__(self, other):
        if isinstance(other, Article):
            return self.id == other.id and self.source == other.source
        return False


class NewsDigest(BaseModel):
    """뉴스 다이제스트 (알림 페이로드)"""

    papers: list[Article] = Field(default_factory=list, description="논문")
    company_news: list[Article] = Field(default_factory=list, description="회사 발표")
    industry_news: list[Article] = Field(default_factory=list, description="산업 뉴스")
    generated_at: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def has_content(self) -> bool:
        return bool(self.papers or self.company_news or self.industry_news)

    @computed_field
    @property
    def total_count(self) -> int:
        return len(self.papers) + len(self.company_news) + len(self.industry_news)

    @classmethod
    def from_articles(cls, articles: list[Article]) -> "NewsDigest":
        """기사 목록에서 다이제스트 생성"""
        papers = []
        company_news = []
        industry_news = []

        for article in articles:
            if article.category == ArticleCategory.PAPER:
                papers.append(article)
            elif article.category == ArticleCategory.COMPANY_NEWS:
                company_news.append(article)
            else:
                industry_news.append(article)

        # 중요도 점수 기준 정렬
        def sort_key(a: Article) -> float:
            return a.importance_score if a.importance_score is not None else 0.0

        papers.sort(key=sort_key, reverse=True)
        company_news.sort(key=sort_key, reverse=True)
        industry_news.sort(key=sort_key, reverse=True)

        return cls(papers=papers, company_news=company_news, industry_news=industry_news)
