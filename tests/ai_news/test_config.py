"""AI News 설정 및 상수 테스트"""
import pytest

from src.ai_news.utils.config import (
    AI_KEYWORDS,
    ARXIV_CATEGORIES,
    CRAWL_SOURCES,
    Config,
    get_config,
)
from src.ai_news.storage.models import ArticleSource, SOURCE_CATEGORY_MAP


class TestConfig:
    """Config 클래스 검증"""

    def test_default_values(self, monkeypatch):
        """환경변수 없을 때 기본값 확인"""
        monkeypatch.delenv("AI_NEWS_S3_BUCKET", raising=False)
        monkeypatch.delenv("S3_BUCKET", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("BEDROCK_REGION", raising=False)
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        monkeypatch.delenv("AI_NEWS_IMPORTANCE_THRESHOLD", raising=False)
        monkeypatch.delenv("AI_NEWS_MAX_PER_SOURCE", raising=False)

        config = Config()
        assert config.aws_region == "ap-northeast-2"
        assert config.bedrock_region == "us-west-2"
        assert config.importance_threshold == 0.5
        assert config.max_articles_per_source == 20

    def test_get_config(self):
        """get_config() 함수 동작 확인"""
        config = get_config()
        assert isinstance(config, Config)


class TestAIKeywords:
    """AI_KEYWORDS 상수 검증"""

    def test_keywords_not_empty(self):
        """키워드 목록이 비어있지 않은지 확인"""
        assert len(AI_KEYWORDS) > 0

    def test_core_keywords_present(self):
        """핵심 키워드 존재 확인"""
        core = ["AI", "LLM", "GPT", "Claude", "transformer", "RAG", "embedding"]
        for keyword in core:
            assert keyword in AI_KEYWORDS, f"핵심 키워드 '{keyword}'가 AI_KEYWORDS에 없음"

    def test_korean_keywords_present(self):
        """한국어 키워드 존재 확인"""
        korean = ["인공지능", "딥러닝", "머신러닝"]
        for keyword in korean:
            assert keyword in AI_KEYWORDS, f"한국어 키워드 '{keyword}'가 AI_KEYWORDS에 없음"

    def test_no_duplicates(self):
        """중복 키워드 없는지 확인"""
        assert len(AI_KEYWORDS) == len(set(AI_KEYWORDS))


class TestArxivCategories:
    """ARXIV_CATEGORIES 상수 검증"""

    def test_categories_count(self):
        """4개 카테고리 확인"""
        assert len(ARXIV_CATEGORIES) == 4

    def test_expected_categories(self):
        """예상 카테고리 확인"""
        expected = ["cs.AI", "cs.CL", "cs.CV", "cs.LG"]
        assert sorted(ARXIV_CATEGORIES) == sorted(expected)


class TestCrawlSources:
    """CRAWL_SOURCES 상수 검증"""

    def test_source_count(self):
        """17개 소스 설정 확인"""
        assert len(CRAWL_SOURCES) == 17

    def test_all_article_sources_have_config(self):
        """모든 ArticleSource에 대응하는 CRAWL_SOURCES 설정 존재 확인"""
        for source in ArticleSource:
            assert source.value in CRAWL_SOURCES, f"{source.value}가 CRAWL_SOURCES에 없음"

    def test_each_source_has_name(self):
        """모든 소스에 name 필드 존재 확인"""
        for key, config in CRAWL_SOURCES.items():
            assert "name" in config, f"CRAWL_SOURCES['{key}']에 name 필드 없음"

    def test_arxiv_has_feeds(self):
        """arXiv 소스에 feeds 설정 확인"""
        arxiv = CRAWL_SOURCES["arxiv"]
        assert "feeds" in arxiv
        assert len(arxiv["feeds"]) == len(ARXIV_CATEGORIES)
