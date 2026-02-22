"""AI News 데이터 모델 테스트"""
import pytest

from src.ai_news.storage.models import (
    Article,
    ArticleCategory,
    ArticleSource,
    NewsDigest,
    SOURCE_CATEGORY_MAP,
)


class TestArticleSource:
    """ArticleSource enum 검증"""

    def test_source_count(self):
        """17개 소스 존재 확인"""
        assert len(ArticleSource) == 17

    def test_source_values(self):
        """주요 소스 값 확인"""
        assert ArticleSource.ARXIV.value == "arxiv"
        assert ArticleSource.HACKERNEWS.value == "hackernews"
        assert ArticleSource.ANTHROPIC.value == "anthropic"
        assert ArticleSource.OPENAI.value == "openai"
        assert ArticleSource.DEEPMIND.value == "deepmind"

    def test_source_is_str_enum(self):
        """str Enum인지 확인"""
        assert isinstance(ArticleSource.ARXIV, str)
        assert ArticleSource.ARXIV == "arxiv"


class TestArticleCategory:
    """ArticleCategory enum 검증"""

    def test_category_count(self):
        """3개 카테고리 존재 확인"""
        assert len(ArticleCategory) == 3

    def test_category_values(self):
        """카테고리 값 확인"""
        assert ArticleCategory.PAPER.value == "paper"
        assert ArticleCategory.COMPANY_NEWS.value == "company"
        assert ArticleCategory.INDUSTRY_NEWS.value == "industry"


class TestSourceCategoryMap:
    """SOURCE_CATEGORY_MAP 검증"""

    def test_all_sources_mapped(self):
        """모든 ArticleSource가 매핑되어 있는지 확인"""
        for source in ArticleSource:
            assert source in SOURCE_CATEGORY_MAP, f"{source}가 SOURCE_CATEGORY_MAP에 없음"

    def test_paper_sources(self):
        """Paper 카테고리 소스 확인"""
        paper_sources = [s for s, c in SOURCE_CATEGORY_MAP.items() if c == ArticleCategory.PAPER]
        assert ArticleSource.ARXIV in paper_sources
        assert ArticleSource.HUGGINGFACE in paper_sources
        assert ArticleSource.MS_RESEARCH in paper_sources
        assert ArticleSource.GOOGLE_RESEARCH in paper_sources

    def test_company_sources(self):
        """Company News 카테고리 소스 확인"""
        company_sources = [s for s, c in SOURCE_CATEGORY_MAP.items() if c == ArticleCategory.COMPANY_NEWS]
        assert ArticleSource.ANTHROPIC in company_sources
        assert ArticleSource.OPENAI in company_sources
        assert ArticleSource.DEEPMIND in company_sources

    def test_industry_sources(self):
        """Industry News 카테고리 소스 확인"""
        industry_sources = [s for s, c in SOURCE_CATEGORY_MAP.items() if c == ArticleCategory.INDUSTRY_NEWS]
        assert ArticleSource.TECHCRUNCH in industry_sources
        assert ArticleSource.HACKERNEWS in industry_sources
        assert ArticleSource.MEDIUM in industry_sources


class TestArticle:
    """Article 모델 검증"""

    @pytest.fixture
    def sample_article(self):
        """테스트용 기사"""
        return Article(
            id="test-001",
            source=ArticleSource.ARXIV,
            category=ArticleCategory.PAPER,
            title="Attention Is All You Need",
            url="https://arxiv.org/abs/1706.03762",
            authors=["Vaswani, A."],
            importance_score=0.9,
            tags=["transformer", "attention"],
        )

    def test_article_creation(self, sample_article):
        """기본 생성 확인"""
        assert sample_article.id == "test-001"
        assert sample_article.source == ArticleSource.ARXIV
        assert sample_article.category == ArticleCategory.PAPER
        assert sample_article.title == "Attention Is All You Need"

    def test_normalized_title(self, sample_article):
        """정규화된 제목 computed field 확인"""
        normalized = sample_article.normalized_title
        assert normalized == "attentionisallyouneed"

    def test_vector_key(self, sample_article):
        """vector_key computed field 확인"""
        assert sample_article.vector_key == "arxiv_test-001"

    def test_optional_fields_default(self):
        """선택 필드 기본값 확인"""
        article = Article(
            id="test-002",
            source=ArticleSource.HACKERNEWS,
            category=ArticleCategory.INDUSTRY_NEWS,
            title="Test Article",
            url="https://example.com",
        )
        assert article.authors is None
        assert article.published_at is None
        assert article.summary is None
        assert article.ai_summary is None
        assert article.importance_score is None
        assert article.tags == []
        assert article.extra is None

    def test_article_equality(self, sample_article):
        """동일 id+source 기사 동등성 확인"""
        other = Article(
            id="test-001",
            source=ArticleSource.ARXIV,
            category=ArticleCategory.PAPER,
            title="Different Title",
            url="https://example.com",
        )
        assert sample_article == other

    def test_article_inequality(self, sample_article):
        """다른 id 기사 비동등성 확인"""
        other = Article(
            id="test-999",
            source=ArticleSource.ARXIV,
            category=ArticleCategory.PAPER,
            title="Attention Is All You Need",
            url="https://example.com",
        )
        assert sample_article != other


class TestNewsDigest:
    """NewsDigest 모델 검증"""

    def _make_article(self, id: str, source: ArticleSource, score: float = 0.5) -> Article:
        category = SOURCE_CATEGORY_MAP[source]
        return Article(
            id=id, source=source, category=category,
            title=f"Article {id}", url=f"https://example.com/{id}",
            importance_score=score,
        )

    def test_empty_digest(self):
        """빈 다이제스트"""
        digest = NewsDigest()
        assert digest.has_content is False
        assert digest.total_count == 0

    def test_from_articles(self):
        """from_articles 카테고리별 그룹화 확인"""
        articles = [
            self._make_article("1", ArticleSource.ARXIV, 0.9),
            self._make_article("2", ArticleSource.ANTHROPIC, 0.8),
            self._make_article("3", ArticleSource.TECHCRUNCH, 0.7),
            self._make_article("4", ArticleSource.HUGGINGFACE, 0.6),
        ]
        digest = NewsDigest.from_articles(articles)
        assert len(digest.papers) == 2
        assert len(digest.company_news) == 1
        assert len(digest.industry_news) == 1
        assert digest.has_content is True
        assert digest.total_count == 4

    def test_from_articles_sorted_by_importance(self):
        """중요도 점수 기준 내림차순 정렬 확인"""
        articles = [
            self._make_article("1", ArticleSource.ARXIV, 0.3),
            self._make_article("2", ArticleSource.HUGGINGFACE, 0.9),
        ]
        digest = NewsDigest.from_articles(articles)
        assert digest.papers[0].importance_score == 0.9
        assert digest.papers[1].importance_score == 0.3
