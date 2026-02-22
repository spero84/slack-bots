"""ai-news 중복 제거 로직 테스트"""

from src.ai_news.main import _is_similar, deduplicate_articles
from src.ai_news.storage.models import Article, ArticleCategory, ArticleSource


def _make_article(
    title: str,
    source: ArticleSource = ArticleSource.AITIMES,
    id: str | None = None,
) -> Article:
    """테스트용 Article 헬퍼"""
    return Article(
        id=id or title[:10],
        source=source,
        category=ArticleCategory.INDUSTRY_NEWS,
        title=title,
        url=f"https://example.com/{id or title[:10]}",
    )


# --- _is_similar 테스트 ---


class TestIsSimilar:
    def test_identical_titles(self):
        """동일한 제목은 유사도 1.0 → True"""
        assert _is_similar("abc", "abc") is True

    def test_completely_different(self):
        """전혀 다른 제목은 False"""
        assert _is_similar("abcdefghij", "zyxwvutsrq") is False

    def test_below_threshold_not_similar(self):
        """0.7 미만 유사도는 False (기본 임계값 0.7)"""
        # "삼성전자ai반도체" vs "삼성전자클라우드사업" → ~0.5 유사도
        assert _is_similar("삼성전자ai반도체투자확대", "삼성전자클라우드사업진출") is False

    def test_above_threshold_similar(self):
        """0.7 이상 유사도는 True"""
        # 거의 동일한 제목 (한두 단어 차이)
        assert _is_similar(
            "openai새로운ai모델gpt5발표",
            "openai새로운ai모델gpt5공개",
        ) is True

    def test_custom_threshold(self):
        """커스텀 임계값 지정 가능"""
        # 낮은 임계값 → 더 공격적으로 중복 판단
        assert _is_similar("abcdef", "abcxyz", threshold=0.3) is True
        # 높은 임계값 → 더 보수적
        assert _is_similar("abcdef", "abcxyz", threshold=0.9) is False


# --- deduplicate_articles 테스트 ---


class TestDeduplicateArticles:
    def test_exact_duplicate_removed(self):
        """정규화 제목이 동일한 기사는 첫 번째만 유지 (1차 중복 제거)"""
        a1 = _make_article("AI 반도체 시장 전망", source=ArticleSource.ARXIV, id="1")
        a2 = _make_article("AI 반도체 시장 전망", source=ArticleSource.TECHCRUNCH, id="2")
        result = deduplicate_articles([a1, a2])
        assert len(result) == 1
        assert result[0].id == "1"

    def test_different_articles_kept(self):
        """다른 제목의 기사는 모두 유지"""
        a1 = _make_article("AI 반도체 시장 전망", id="1")
        a2 = _make_article("클라우드 보안 기술 동향", id="2")
        result = deduplicate_articles([a1, a2])
        assert len(result) == 2

    def test_korean_source_similar_removed(self):
        """한국 소스 간 유사 제목 (0.7 이상)은 중복 제거됨"""
        a1 = _make_article(
            "OpenAI 새로운 AI 모델 GPT-5 발표",
            source=ArticleSource.AITIMES,
            id="1",
        )
        a2 = _make_article(
            "OpenAI 새로운 AI 모델 GPT-5 공개",
            source=ArticleSource.ETNEWS,
            id="2",
        )
        result = deduplicate_articles([a1, a2])
        assert len(result) == 1
        assert result[0].id == "1"

    def test_korean_source_dissimilar_kept(self):
        """한국 소스여도 유사도 0.7 미만이면 유지"""
        a1 = _make_article(
            "삼성전자 AI 반도체 투자 확대",
            source=ArticleSource.AITIMES,
            id="1",
        )
        a2 = _make_article(
            "네이버 클라우드 사업 진출 발표",
            source=ArticleSource.ETNEWS,
            id="2",
        )
        result = deduplicate_articles([a1, a2])
        assert len(result) == 2

    def test_non_korean_source_not_deduplicated(self):
        """비한국 소스는 유사 제목이어도 2차 중복 제거 대상 아님"""
        a1 = _make_article(
            "OpenAI Releases New GPT-5 Model",
            source=ArticleSource.TECHCRUNCH,
            id="1",
        )
        a2 = _make_article(
            "OpenAI Releases New GPT-5 Model Today",
            source=ArticleSource.HACKERNEWS,
            id="2",
        )
        result = deduplicate_articles([a1, a2])
        assert len(result) == 2

    def test_empty_list(self):
        """빈 리스트 입력 시 빈 리스트 반환"""
        assert deduplicate_articles([]) == []

    def test_mixed_sources(self):
        """한국/비한국 소스 혼합 시 한국 소스 간에만 유사 중복 제거"""
        a1 = _make_article(
            "구글 제미나이 2.0 출시",
            source=ArticleSource.AITIMES,
            id="1",
        )
        a2 = _make_article(
            "구글 제미나이 2.0 공개",
            source=ArticleSource.ITWORLD,
            id="2",
        )
        a3 = _make_article(
            "Google Gemini 2.0 Released",
            source=ArticleSource.TECHCRUNCH,
            id="3",
        )
        result = deduplicate_articles([a1, a2, a3])
        # a1과 a2는 한국 소스 간 유사 → a2 제거, a3은 비한국이므로 유지
        assert len(result) == 2
        ids = {a.id for a in result}
        assert "1" in ids
        assert "3" in ids
