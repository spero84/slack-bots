"""RSS 기반 크롤러 회귀 테스트 (arXiv, OpenAI, ETNews)

세 소스 모두 실제 운영에서 조용히 고장나 있었다.
- arXiv: export.arxiv.org/rss/{cat}가 유효하지만 항목 0건인 빈 피드를 반환
- OpenAI: HTML 페이지 전부 403 Forbidden (봇 차단)
- ETNews: /news/article_list/AI 경로 404

네트워크를 타지 않도록 실제 피드 XML을 픽스처로 파싱한다.
"""
import pytest

# feedparser는 ai-news 전용 의존성이다 (다른 서비스 venv에서는 스킵)
feedparser = pytest.importorskip("feedparser")

from src.ai_news.crawlers import arxiv_crawler  # noqa: E402
from src.ai_news.crawlers.arxiv_crawler import ArxivCrawler  # noqa: E402
from src.ai_news.crawlers.etnews_crawler import ETNewsCrawler  # noqa: E402
from src.ai_news.crawlers.openai_crawler import OpenAICrawler  # noqa: E402
from src.ai_news.utils.config import CRAWL_SOURCES  # noqa: E402

# arxiv_crawler.feedparser는 feedparser 모듈 자체라, parse를 패치하면
# 패치한 함수 안에서 feedparser.parse를 부를 때 무한 재귀가 된다.
# 원본을 미리 붙잡아 둔다.
_real_parse = feedparser.parse

# --- 픽스처 XML ---

ARXIV_XML = """<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom"
     xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel>
    <title>{category} updates on arXiv.org</title>
    <item>
      <title>TANGO: Humanoid Navigation in Cluttered Environments</title>
      <link>https://arxiv.org/abs/2609.09158</link>
      <description>arXiv:2609.09158v1 Announce Type: new
Abstract: We present TANGO, a whole-body policy.</description>
      <dc:creator>Anqi Li, Yuxin Chen</dc:creator>
      <arxiv:announce_type>new</arxiv:announce_type>
      <pubDate>Wed, 09 Sep 2026 00:00:00 -0400</pubDate>
    </item>
    <item>
      <title>Old Paper Revised Again</title>
      <link>https://arxiv.org/abs/2501.11111</link>
      <description>arXiv:2501.11111v3 Announce Type: replace
Abstract: Revision of an old paper.</description>
      <arxiv:announce_type>replace</arxiv:announce_type>
      <pubDate>Wed, 09 Sep 2026 00:00:00 -0400</pubDate>
    </item>
    <item>
      <title>Cross Listed Retrieval Work</title>
      <link>https://arxiv.org/abs/2609.22222</link>
      <description>arXiv:2609.22222v1 Announce Type: cross
Abstract: A cross-listed retrieval paper.</description>
      <arxiv:announce_type>cross</arxiv:announce_type>
      <pubDate>Wed, 09 Sep 2026 00:00:00 -0400</pubDate>
    </item>
    <item>
      <title>Cross Listed But Revised</title>
      <link>https://arxiv.org/abs/2502.33333</link>
      <description>arXiv:2502.33333v2 Announce Type: replace-cross
Abstract: Revision of a cross-listed paper.</description>
      <arxiv:announce_type>replace-cross</arxiv:announce_type>
      <pubDate>Wed, 09 Sep 2026 00:00:00 -0400</pubDate>
    </item>
  </channel>
</rss>
"""

ARXIV_EMPTY_XML = """<?xml version='1.0' encoding='UTF-8'?>
<rss version="2.0"><channel><title>cs.AI updates</title></channel></rss>
"""

OPENAI_XML = """<?xml version='1.0' encoding='UTF-8'?>
<rss version="2.0"><channel>
  <title>OpenAI News</title>
  <item>
    <title>Introducing ChatGPT Images 2.5</title>
    <link>https://openai.com/index/introducing-chatgpt-images-2-5</link>
    <description>ChatGPT Images 2.5 turns sketches into polished art.</description>
    <category>Product</category>
    <pubDate>Tue, 08 Sep 2026 11:30:00 GMT</pubDate>
  </item>
  <item>
    <title>On the Navier-Stokes Millennium Prize Problem</title>
    <link>https://openai.com/index/navier-stokes-solution</link>
    <description>An AI-generated solution.</description>
    <category>Research</category>
    <pubDate>Tue, 08 Sep 2026 10:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

ETNEWS_AI_XML = """<?xml version='1.0' encoding='UTF-8'?>
<rss version="2.0"><channel>
  <title>전자신문 AI</title>
  <item>
    <title>한국오라클 클라우드 매출 1000억 육박</title>
    <link>https://www.etnews.com/20260909000271</link>
    <description>한국오라클의 클라우드 사업 부문 매출이 두 배 증가했다.</description>
    <pubDate>Wed, 9 Sep 2026 17:00:00 +0900</pubDate>
  </item>
</channel></rss>
"""

ETNEWS_SW_XML = """<?xml version='1.0' encoding='UTF-8'?>
<rss version="2.0"><channel>
  <title>전자신문 SW</title>
  <item>
    <title>AI 가속기 전력비율로 AIDC 규정</title>
    <link>https://www.etnews.com/20260909000387</link>
    <description>정부가 인공지능데이터센터를 규정했다.</description>
    <pubDate>Wed, 9 Sep 2026 17:19:48 +0900</pubDate>
  </item>
  <item>
    <title>넥슨재단, 경복궁서 전통공예 전시</title>
    <link>https://www.etnews.com/20260909000401</link>
    <description>게임 지식재산과 전통공예를 결합한 작품을 선보인다.</description>
    <pubDate>Wed, 9 Sep 2026 18:19:18 +0900</pubDate>
  </item>
  <item>
    <title>한국오라클 클라우드 매출 1000억 육박</title>
    <link>https://www.etnews.com/20260909000271</link>
    <description>AI 섹션과 중복되는 기사.</description>
    <pubDate>Wed, 9 Sep 2026 17:00:00 +0900</pubDate>
  </item>
</channel></rss>
"""


class _FakeResponse:
    """requests.Response 대역"""

    def __init__(self, body: str):
        self.content = body.encode()
        self.text = body

    def raise_for_status(self):
        pass


# --- arXiv ---

class TestArxivCrawler:
    """arXiv RSS 크롤러"""

    def test_feed_url_uses_live_host(self):
        """빈 피드를 주는 export.arxiv.org를 쓰지 않는지"""
        feeds = CRAWL_SOURCES["arxiv"]["feeds"]
        assert feeds
        for url in feeds.values():
            assert "rss.arxiv.org" in url
            assert "export.arxiv.org" not in url

    @pytest.mark.asyncio
    async def test_skips_revisions(self, monkeypatch):
        """replace / replace-cross(개정판)는 제외하고 new / cross만 수집한다"""
        monkeypatch.setattr(
            arxiv_crawler.feedparser,
            "parse",
            lambda url: _real_parse(ARXIV_XML.format(category="cs.AI")),
        )
        crawler = ArxivCrawler()
        crawler.feeds = {"cs.AI": "https://rss.arxiv.org/rss/cs.AI"}

        articles = await crawler.crawl(max_items=20)

        assert [a.id for a in articles] == ["2609.09158", "2609.22222"]

    @pytest.mark.asyncio
    async def test_preserves_title_with_colon(self, monkeypatch):
        """콜론이 든 제목의 앞부분이 잘리지 않는지 (기존 접두어 제거 버그)"""
        monkeypatch.setattr(
            arxiv_crawler.feedparser,
            "parse",
            lambda url: _real_parse(ARXIV_XML.format(category="cs.AI")),
        )
        crawler = ArxivCrawler()
        crawler.feeds = {"cs.AI": "https://rss.arxiv.org/rss/cs.AI"}

        articles = await crawler.crawl(max_items=20)

        assert articles[0].title == (
            "TANGO: Humanoid Navigation in Cluttered Environments"
        )

    @pytest.mark.asyncio
    async def test_strips_summary_prefix(self, monkeypatch):
        """초록에서 arXiv ID / Announce Type / Abstract 접두어를 제거한다"""
        monkeypatch.setattr(
            arxiv_crawler.feedparser,
            "parse",
            lambda url: _real_parse(ARXIV_XML.format(category="cs.AI")),
        )
        crawler = ArxivCrawler()
        crawler.feeds = {"cs.AI": "https://rss.arxiv.org/rss/cs.AI"}

        articles = await crawler.crawl(max_items=20)

        assert articles[0].summary == "We present TANGO, a whole-body policy."

    @pytest.mark.asyncio
    async def test_interleaves_categories(self, monkeypatch):
        """모든 published가 동일하므로 카테고리를 번갈아 뽑는다"""
        def fake_parse(url):
            category = url.rsplit("/", 1)[-1]
            # 카테고리마다 다른 ID를 주도록 링크를 치환
            suffix = str(abs(hash(category)) % 90 + 10)
            xml = ARXIV_XML.format(category=category).replace(
                "2609.09158", f"2609.091{suffix}"
            ).replace("2609.22222", f"2609.222{suffix}")
            return _real_parse(xml)

        monkeypatch.setattr(arxiv_crawler.feedparser, "parse", fake_parse)
        crawler = ArxivCrawler()
        crawler.feeds = {
            "cs.AI": "https://rss.arxiv.org/rss/cs.AI",
            "cs.IR": "https://rss.arxiv.org/rss/cs.IR",
        }

        articles = await crawler.crawl(max_items=4)

        # 첫 두 건이 서로 다른 카테고리에서 나와야 한다
        assert articles[0].tags[0] != articles[1].tags[0]

    @pytest.mark.asyncio
    async def test_empty_feed_warns_and_returns_nothing(self, monkeypatch, caplog):
        """빈 피드는 정상 응답으로 위장되므로 경고를 남긴다"""
        monkeypatch.setattr(
            arxiv_crawler.feedparser,
            "parse",
            lambda url: _real_parse(ARXIV_EMPTY_XML),
        )
        crawler = ArxivCrawler()
        crawler.feeds = {"cs.AI": "https://rss.arxiv.org/rss/cs.AI"}

        with caplog.at_level("WARNING"):
            articles = await crawler.crawl(max_items=20)

        assert articles == []
        assert "비어 있음" in caplog.text


# --- OpenAI ---

class TestOpenAICrawler:
    """OpenAI RSS 크롤러"""

    def test_config_has_rss_url(self):
        """HTML 대신 RSS를 1순위로 쓰는지"""
        assert CRAWL_SOURCES["openai"]["rss_url"] == "https://openai.com/news/rss.xml"

    @pytest.mark.asyncio
    async def test_crawls_rss(self, monkeypatch):
        """RSS에서 제목/URL/게시일/카테고리 태그를 수집한다"""
        crawler = OpenAICrawler()
        monkeypatch.setattr(
            crawler.session, "get", lambda url, **kw: _FakeResponse(OPENAI_XML)
        )

        articles = await crawler.crawl(max_items=20)

        assert len(articles) == 2
        first = articles[0]
        assert first.id == "introducing-chatgpt-images-2-5"
        assert first.title == "Introducing ChatGPT Images 2.5"
        assert first.url == "https://openai.com/index/introducing-chatgpt-images-2-5"
        assert first.published_at is not None
        assert "openai" in first.tags and "Product" in first.tags

    @pytest.mark.asyncio
    async def test_respects_max_items(self, monkeypatch):
        """max_items를 초과하지 않는다"""
        crawler = OpenAICrawler()
        monkeypatch.setattr(
            crawler.session, "get", lambda url, **kw: _FakeResponse(OPENAI_XML)
        )

        assert len(await crawler.crawl(max_items=1)) == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_html_when_rss_fails(self, monkeypatch):
        """RSS가 실패하면 HTML 스크래핑으로 폴백한다"""
        crawler = OpenAICrawler()

        def boom(url, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(crawler.session, "get", boom)
        called = {}

        def fake_html(max_items):
            called["yes"] = max_items
            return []

        monkeypatch.setattr(crawler, "_crawl_html", fake_html)

        assert await crawler.crawl(max_items=7) == []
        assert called == {"yes": 7}

    def test_403_logged_as_warning_not_error(self, monkeypatch, caplog):
        """403은 차단 경고로 남아야 한다 (Response.__bool__ 함정)"""
        import requests

        crawler = OpenAICrawler()
        response = requests.Response()
        response.status_code = 403

        def raise_403(url, **kw):
            raise requests.exceptions.HTTPError(response=response)

        monkeypatch.setattr(crawler.session, "get", raise_403)

        with caplog.at_level("WARNING"):
            assert crawler._crawl_html(max_items=5) == []

        assert "접근 차단 (403)" in caplog.text
        assert not [r for r in caplog.records if r.levelname == "ERROR"]


# --- ETNews ---

class TestETNewsCrawler:
    """ETNews 섹션 RSS 크롤러"""

    def test_config_has_section_feeds(self):
        """404 나는 article_list 경로를 쓰지 않는지"""
        feeds = CRAWL_SOURCES["etnews"]["feeds"]
        assert len(feeds) == 2
        for url, _ in feeds:
            assert url.startswith("https://rss.etnews.com/")
            assert "article_list" not in url

    @pytest.mark.asyncio
    async def test_filters_sw_feed_by_keyword(self, monkeypatch):
        """AI 섹션은 그대로, SW 섹션은 AI 키워드로 걸러낸다"""
        crawler = ETNewsCrawler()
        bodies = {
            "https://rss.etnews.com/04046.xml": ETNEWS_AI_XML,
            "https://rss.etnews.com/04043.xml": ETNEWS_SW_XML,
        }
        monkeypatch.setattr(
            crawler.session, "get", lambda url, **kw: _FakeResponse(bodies[url])
        )

        articles = await crawler.crawl(max_items=20)
        titles = [a.title for a in articles]

        assert "한국오라클 클라우드 매출 1000억 육박" in titles
        assert "AI 가속기 전력비율로 AIDC 규정" in titles
        # AI 키워드가 없는 SW 섹션 기사는 제외
        assert "넥슨재단, 경복궁서 전통공예 전시" not in titles

    @pytest.mark.asyncio
    async def test_dedupes_across_feeds(self, monkeypatch):
        """두 피드에 같은 기사가 있어도 한 번만 담는다"""
        crawler = ETNewsCrawler()
        bodies = {
            "https://rss.etnews.com/04046.xml": ETNEWS_AI_XML,
            "https://rss.etnews.com/04043.xml": ETNEWS_SW_XML,
        }
        monkeypatch.setattr(
            crawler.session, "get", lambda url, **kw: _FakeResponse(bodies[url])
        )

        articles = await crawler.crawl(max_items=20)
        ids = [a.id for a in articles]

        assert len(ids) == len(set(ids))
        assert ids.count("20260909000271") == 1

    @pytest.mark.asyncio
    async def test_parses_published_at(self, monkeypatch):
        """RSS는 게시일을 제공하므로 published_at이 채워진다"""
        crawler = ETNewsCrawler()
        monkeypatch.setattr(
            crawler.session, "get", lambda url, **kw: _FakeResponse(ETNEWS_AI_XML)
        )

        articles = await crawler.crawl(max_items=1)

        assert articles[0].published_at is not None
        assert articles[0].id == "20260909000271"

    @pytest.mark.asyncio
    async def test_falls_back_to_html_when_feeds_fail(self, monkeypatch):
        """모든 피드가 실패하면 HTML 스크래핑으로 폴백한다"""
        crawler = ETNewsCrawler()

        def boom(url, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(crawler.session, "get", boom)
        called = {}
        monkeypatch.setattr(
            crawler,
            "_crawl_html",
            lambda max_items: called.setdefault("yes", max_items) and [] or [],
        )

        assert await crawler.crawl(max_items=9) == []
        assert called == {"yes": 9}
