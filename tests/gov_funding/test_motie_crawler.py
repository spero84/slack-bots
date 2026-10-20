"""MotieCrawler 테스트 (실제 산업통상부 www.motir.go.kr 페이지를 축약한 fixture 사용)

- 상세 URL은 '/{id}/view' — '/view'가 없으면 404.
- 첨부파일 링크 텍스트 끝에 ' [178.7 KB]'가 붙어 확장자 판별이 깨진다 → 제거.
- 첫 열 공고번호(2026-643)는 분야(category)가 아니다.
"""
import contextlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from bs4 import BeautifulSoup

from src.gov_funding.crawlers import motie_crawler
from src.gov_funding.crawlers.motie_crawler import (
    MotieCrawler,
    parse_motie_detail,
    parse_motie_list,
    parse_motie_row,
    strip_size_suffix,
)
from src.gov_funding.storage import Announcement, Source
from src.gov_funding.utils import CRAWL_SOURCES

FIXTURES = Path(__file__).parent / "fixtures" / "motie"
DETAIL_TEMPLATE = "https://www.motir.go.kr/kor/article/ATCL2826a2625/{id}/view"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fake_response(text: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.status_code = status
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} Error", response=resp)
    return resp


@pytest.fixture
def crawler():
    c = MotieCrawler(page_delay=0)
    yield c
    c.session.close()


class TestConfig:
    def test_uses_motir_domain(self):
        """motie.go.kr는 motir.go.kr로 301 리다이렉트 — 처음부터 motir 사용"""
        config = CRAWL_SOURCES["motie"]
        assert config["base_url"] == "https://www.motir.go.kr"
        assert config["list_url"] == "https://www.motir.go.kr/kor/article/ATCL2826a2625"

    def test_detail_url_has_view_suffix(self, crawler):
        assert CRAWL_SOURCES["motie"]["detail_url_template"] == DETAIL_TEMPLATE
        url = crawler.detail_url("71365")
        assert url == "https://www.motir.go.kr/kor/article/ATCL2826a2625/71365/view"
        assert "?" not in url


class TestParseList:
    def test_rows(self):
        items = parse_motie_list(read_fixture("list_p1.html"))

        assert [a.id for a in items] == ["71374", "71373", "71370", "71365"]
        ann = items[3]
        assert ann.title == (
            "2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고 "
            "(공정·응용 솔루션 AX R&D, 응용 제품 AX R&D)"
        )
        assert ann.source == Source.MOTIE
        assert ann.organization == "인공지능기계로봇과"
        assert ann.posted_date.date().isoformat() == "2026-10-13"

    def test_category_is_not_notice_number(self):
        """공고번호(2026-643)를 분야로 넣으면 Slack에 '분야: 2026-643'으로 표시된다"""
        assert all(a.category is None for a in parse_motie_list(read_fixture("list_p1.html")))

    def test_url_is_canonical(self):
        """목록 링크의 '?mno=&pageIndex=1'을 버리고 정규 URL 사용 (vector key는 ID라 불변)"""
        for ann in parse_motie_list(read_fixture("list_p1.html")):
            assert ann.url == DETAIL_TEMPLATE.format(id=ann.id)

    def test_empty_board_row_skipped(self):
        html = "<table><tbody><tr><td colspan='6'>등록된 게시물이 없습니다.</td></tr></tbody></table>"
        assert parse_motie_list(html) == []

    def test_multi_cell_empty_board_row_skipped_without_warning(self, caplog):
        html = """<table><tbody><tr>
          <td></td><td>등록된 게시물이 없습니다.</td><td></td><td></td><td></td><td></td>
        </tr></tbody></table>"""
        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert parse_motie_list(html) == []
        assert not caplog.records

    def test_title_containing_registered_is_kept(self):
        """'등록된'이 들어간 실제 공고를 빈 게시판 행으로 오인하지 않는다"""
        title = "혁신제품으로 등록된 중소기업 AI 실증 지원사업 공고"
        html = f"""<table><tbody><tr>
          <td>2026-701</td>
          <td><a href="#none" onclick="article.view('71401'); return false;"><i>{title}</i></a></td>
          <td>혁신행정담당관</td><td>2026-10-20</td><td>3</td><td></td>
        </tr></tbody></table>"""

        items = parse_motie_list(html)

        assert [(a.id, a.title) for a in items] == [("71401", title)]

    def test_id_from_href_when_no_onclick(self):
        html = """<table><tbody><tr>
          <td>2026-700</td>
          <td><a href="/kor/article/ATCL2826a2625/71400/view?mno=&amp;pageIndex=2"><i>테스트 공고</i></a></td>
          <td>테스트과</td><td>2026-10-20</td><td>1</td><td></td>
        </tr></tbody></table>"""
        row = BeautifulSoup(html, "lxml").select_one("tbody tr")

        ann = parse_motie_row(row, DETAIL_TEMPLATE)

        assert ann.id == "71400"
        assert ann.url == DETAIL_TEMPLATE.format(id="71400")

    def test_row_without_article_id_is_skipped(self, caplog):
        """게시물 번호가 없으면 공고번호/제목 해시로 대체하지 않고 건너뜀 (404 링크·키 변경 방지)"""
        html = """<table><tbody><tr>
          <td>2026-700</td>
          <td><a href="#none" onclick="article.view('')"><i>테스트 공고</i></a></td>
          <td>테스트과</td><td>2026-10-20</td><td>1</td><td></td>
        </tr></tbody></table>"""
        row = BeautifulSoup(html, "lxml").select_one("tbody tr")

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert parse_motie_row(row, DETAIL_TEMPLATE) is None
        assert any("게시물 번호" in r.message for r in caplog.records)


class TestParseDetail:
    def test_content_from_detail_cont(self):
        detail = parse_motie_detail(read_fixture("detail_71365.html"))

        content = detail["content"]
        assert content.startswith("2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고")
        assert "신청하여 주시기 바랍니다." in content
        # 첨부파일·메타 정보는 본문에 섞이지 않는다
        assert "KB]" not in content
        assert "조회수" not in content

    def test_attachments_strip_size_and_skip_viewer(self):
        detail = parse_motie_detail(read_fixture("detail_71365.html"))

        names = [a["name"] for a in detail["attachments"]]
        assert names == [
            "(공고번호제2026-643호) 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고.hwpx",
            "(공고번호제2026-643호) 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고.pdf",
        ]
        for att in detail["attachments"]:
            assert att["url"].startswith("https://www.motir.go.kr/attach/down/")
            assert "/attach/viewer/" not in att["url"]

    def test_not_found_page_returns_none(self):
        assert parse_motie_detail(read_fixture("not_found.html")) is None


@pytest.mark.parametrize("raw,expected", [
    ("공고.hwpx [178.7 KB]", "공고.hwpx"),
    ("공고.pdf [623 KB]", "공고.pdf"),
    ("신청서.hwp [1,024 KB]", "신청서.hwp"),
    ("큰파일.zip [1.2 MB]", "큰파일.zip"),
    ("작은파일.txt [512 bytes]", "작은파일.txt"),
    ("[첨부1] 확인 신청서.hwpx [33 KB]", "[첨부1] 확인 신청서.hwpx"),
    ("크기없음.hwpx", "크기없음.hwpx"),
])
def test_strip_size_suffix(raw, expected):
    assert strip_size_suffix(raw) == expected


class TestGetDetail:
    @pytest.mark.asyncio
    async def test_fetches_view_url_with_requests(self, crawler, monkeypatch):
        calls = []

        def fake_get(url, timeout):
            calls.append(url)
            return fake_response(read_fixture("detail_71365.html"))

        monkeypatch.setattr(crawler.session, "get", fake_get)

        detail = await crawler.get_detail("71365")

        assert calls == ["https://www.motir.go.kr/kor/article/ATCL2826a2625/71365/view"]
        assert [a["name"].rsplit(".", 1)[-1] for a in detail["attachments"]] == ["hwpx", "pdf"]
        assert crawler.browser is None, "정적 페이지인데 브라우저를 띄웠다"

    @pytest.mark.asyncio
    async def test_error_page_returns_none_with_warning(self, crawler, monkeypatch, caplog):
        """HTTP 200 오류 페이지(.board-detail 없음)도 WARNING + None"""
        monkeypatch.setattr(
            crawler.session, "get", lambda url, timeout: fake_response(read_fixture("not_found.html"))
        )

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert await crawler.get_detail("71365") is None
        assert any(".board-detail" in r.message for r in caplog.records if r.levelname == "WARNING")

    @pytest.mark.asyncio
    async def test_404_does_not_retry_with_browser(self, crawler, monkeypatch, caplog):
        async def fail_browser(url):
            raise AssertionError("404인데 브라우저로 재시도했다")

        monkeypatch.setattr(crawler.session, "get", lambda url, timeout: fake_response("", status=404))
        monkeypatch.setattr(crawler, "_fetch_html_with_browser", fail_browser)

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert await crawler.get_detail("71365") is None
        assert any("404" in r.message for r in caplog.records if r.levelname == "WARNING")

    @pytest.mark.asyncio
    async def test_falls_back_to_browser_when_requests_fails(self, crawler, monkeypatch):
        def broken_get(url, timeout):
            raise requests.Timeout("timed out")

        async def fake_browser_fetch(url):
            assert url.endswith("/71365/view")
            return read_fixture("detail_71365.html")

        monkeypatch.setattr(crawler.session, "get", broken_get)
        monkeypatch.setattr(crawler, "_fetch_html_with_browser", fake_browser_fetch)

        detail = await crawler.get_detail("71365")

        assert len(detail["attachments"]) == 2


# ---------------------------------------------------------------- 목록 크롤링


class FakePage:
    def __init__(self, html: str = ""):
        self.closed = False
        self.url = CRAWL_SOURCES["motie"]["list_url"]
        self._html = html

    async def goto(self, url, **kwargs):
        self.url = url

    async def content(self):
        return self._html

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page):
        self.page = page

    def is_connected(self):
        return True

    async def new_page(self):
        return self.page

    async def close(self):
        pass


def make_items(page_num: int, n: int = 10) -> list[Announcement]:
    return [
        Announcement(
            id=f"{page_num}{i:02d}",
            source=Source.MOTIE,
            title=f"공고 {page_num}-{i}",
            url=DETAIL_TEMPLATE.format(id=f"{page_num}{i:02d}"),
        )
        for i in range(n)
    ]


@pytest.fixture
def fake_listing(crawler, monkeypatch):
    monkeypatch.setattr(motie_crawler, "PLAYWRIGHT_AVAILABLE", True)
    page = FakePage()
    state = {"page": 1, "next_calls": 0}

    async def fake_init_browser():
        crawler.browser = FakeBrowser(page)

    async def fake_parse_page(_page):
        return make_items(state["page"])

    async def fake_go_next(_page, current):
        state["next_calls"] += 1
        state["page"] = current + 1
        return True

    monkeypatch.setattr(crawler, "_init_browser", fake_init_browser)
    monkeypatch.setattr(crawler, "_parse_page", fake_parse_page)
    monkeypatch.setattr(crawler, "_go_next_page", fake_go_next)
    return state, page


class TestCrawl:
    @pytest.mark.asyncio
    async def test_no_extra_page_when_enough_items(self, crawler, fake_listing):
        state, page = fake_listing

        result = await crawler.crawl(max_items=5)

        assert len(result) == 5
        assert state["next_calls"] == 0
        assert page.closed

    @pytest.mark.asyncio
    async def test_stops_at_max_pages_without_extra_navigation(self, crawler, fake_listing):
        state, _ = fake_listing

        result = await crawler.crawl(max_items=50)

        assert len(result) == 50
        assert state["next_calls"] == 4

    @pytest.mark.asyncio
    async def test_parse_page_uses_page_html(self, crawler, monkeypatch):
        """목록 행 파싱은 page.content()를 BeautifulSoup으로 처리"""
        monkeypatch.setattr(motie_crawler, "PLAYWRIGHT_AVAILABLE", True)
        page = FakePage(read_fixture("list_p1.html"))

        async def fake_init_browser():
            crawler.browser = FakeBrowser(page)

        monkeypatch.setattr(crawler, "_init_browser", fake_init_browser)

        result = await crawler.crawl(max_items=3)

        assert [a.id for a in result] == ["71374", "71373", "71370"]
        assert all(a.url == DETAIL_TEMPLATE.format(id=a.id) for a in result)

    @pytest.mark.asyncio
    async def test_keeps_collected_pages_when_later_page_fails(self, crawler, fake_listing, monkeypatch, caplog):
        """2페이지 page.content()가 'page is navigating'으로 실패해도 1페이지 결과는 유지 (예전에는 0건 반환)"""
        state, page = fake_listing

        async def flaky_parse_page(_page):
            if state["page"] == 1:
                return make_items(1)
            raise Exception(
                "Page.content: Unable to retrieve content because the page is navigating and changing the content."
            )

        monkeypatch.setattr(crawler, "_parse_page", flaky_parse_page)

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            result = await crawler.crawl(max_items=50)

        assert [a.id for a in result] == [a.id for a in make_items(1)]
        assert page.closed
        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("페이지 2" in m and "10건 유지" in m for m in warnings)
        assert not any("크롤링 결과 0건" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_new_page_failure_returns_empty_with_warning(self, crawler, monkeypatch, caplog):
        monkeypatch.setattr(motie_crawler, "PLAYWRIGHT_AVAILABLE", True)

        class DeadBrowser(FakeBrowser):
            async def new_page(self):
                raise Exception("Browser.new_page: Target page, context or browser has been closed")

        async def fake_init_browser():
            crawler.browser = DeadBrowser(None)

        monkeypatch.setattr(crawler, "_init_browser", fake_init_browser)

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert await crawler.crawl(max_items=5) == []
        assert any("0건" in r.message for r in caplog.records if r.levelname == "WARNING")


class NavPage(FakePage):
    """expect_navigation/evaluate/goto 호출 순서를 기록하는 가짜 Page"""

    def __init__(self, evaluate_error: Exception | None = None):
        super().__init__()
        self.events: list[str] = []
        self.evaluate_error = evaluate_error

    def expect_navigation(self, **kwargs):
        page = self

        @contextlib.asynccontextmanager
        async def waiter():
            page.events.append(f"expect_navigation(wait_until={kwargs.get('wait_until')})")
            yield
            # 실제 Playwright처럼 블록이 끝난 뒤 이동 완료까지 기다린다
            page.events.append("navigation_done")

        return waiter()

    async def evaluate(self, expression):
        self.events.append(f"evaluate:{expression}")
        if self.evaluate_error:
            raise self.evaluate_error

    async def wait_for_load_state(self, *args, **kwargs):
        self.events.append("wait_for_load_state")

    async def goto(self, url, **kwargs):
        self.events.append(f"goto:{url}")
        self.url = url


class TestGoNextPage:
    @pytest.mark.asyncio
    async def test_waits_for_navigation_around_article_list(self, crawler):
        """article.list(N)은 form GET submit → evaluate를 expect_navigation으로 감싸 이동 완료를 기다린다"""
        page = NavPage()

        assert await crawler._go_next_page(page, 1) is True

        assert page.events == [
            "expect_navigation(wait_until=load)",
            "evaluate:article.list(2)",
            "navigation_done",
        ]

    @pytest.mark.asyncio
    async def test_falls_back_to_page_index_url(self, crawler):
        page = NavPage(evaluate_error=Exception("ReferenceError: article is not defined"))

        assert await crawler._go_next_page(page, 2) is True

        assert page.events[-1] == "goto:https://www.motir.go.kr/kor/article/ATCL2826a2625?pageIndex=3"
