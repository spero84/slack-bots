"""MsitCrawler 테스트 (실제 과기정통부 페이지를 축약한 fixture 사용)

- 상세 URL에 bbsSeqNo=100이 없으면 HTTP 200 '시스템 점검 안내' 오류 페이지가 온다.
- 첨부파일은 onclick="fn_download('55355', '1', 'hwpx')" 형태라 href로는 찾을 수 없다.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from bs4 import BeautifulSoup

from src.gov_funding.crawlers import msit_crawler
from src.gov_funding.crawlers.msit_crawler import (
    MsitCrawler,
    is_msit_error_page,
    parse_msit_attachments,
    parse_msit_detail,
)
from src.gov_funding.storage import Announcement, Source
from src.gov_funding.utils import CRAWL_SOURCES

FIXTURES = Path(__file__).parent / "fixtures" / "msit"
FILE_URL = "https://www.msit.go.kr/ssm/file/fileDown.do?atchFileNo={no}&fileOrd={ord}&fileBtn=A"


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
    c = MsitCrawler(page_delay=0)
    yield c
    c.session.close()


class TestConfig:
    def test_detail_url_has_board_id(self):
        """bbsSeqNo=100이 빠지면 '시스템 점검 안내' 오류 페이지 (Slack 미리보기도 동일)"""
        template = CRAWL_SOURCES["msit"]["detail_url_template"]
        assert "bbsSeqNo=100" in template
        assert template.format(id="3186897") == (
            "https://www.msit.go.kr/bbs/view.do?sCode=user&mPid=121&mId=311"
            "&bbsSeqNo=100&nttSeqNo=3186897"
        )

    def test_detail_url_helper(self, crawler):
        assert crawler.detail_url("3186897").endswith("&bbsSeqNo=100&nttSeqNo=3186897")


class TestParseDetail:
    def test_content_from_view_cont(self):
        detail = parse_msit_detail(read_fixture("detail_3186897.html"))

        content = detail["content"]
        assert content.startswith("과학기술정보통신부 공고 제2026–1007호")
        assert "붙임과 같이 공모합니다." in content
        # 쪼개진 <span>이 한 줄로 이어진다
        assert "2026년도 한미 이공분야 학생 인력교류 프로그램(NSF IRES) 운영 지원사업 신규과제 공모" in content
        # 첨부파일 목록·이전/다음글·한글 에디터 JSON 주석은 본문에 섞이지 않는다
        assert "다운로드" not in content
        assert "이전글" not in content
        assert "documentPr" not in content

    def test_attachments_from_fn_download(self):
        detail = parse_msit_detail(read_fixture("detail_3186897.html"))

        name = "2026년도 한미 이공분야 학생 인력교류 프로그램(NSF IRES) 운영 지원사업 신규과제 공고문"
        assert detail["attachments"] == [
            {"name": f"{name}.hwpx", "url": FILE_URL.format(no="55355", ord="1")},
            {"name": f"{name}.hwp", "url": FILE_URL.format(no="55355", ord="3")},
        ]

    def test_attachment_without_download_onclick_skipped(self):
        html = """
        <ul class="down_file">
          <li><a class="ico_file_pdf">a.pdf</a>
              <div class="down_btn"><a class="down" onclick="fn_download(77, 2, 'pdf');">다운로드</a></div></li>
          <li><a class="ico_file_hwp">b.hwp</a><div class="down_btn"><a class="view">바로보기</a></div></li>
          <li><div class="down_btn"><a class="down" onclick="fn_download('1', '1', 'hwp');">다운로드</a></div></li>
        </ul>
        """
        soup = BeautifulSoup(html, "lxml")
        template = CRAWL_SOURCES["msit"]["attachment_url_template"]

        assert parse_msit_attachments(soup, template) == [
            {"name": "a.pdf", "url": FILE_URL.format(no="77", ord="2")},
        ]

    def test_error_page_detected(self):
        html = read_fixture("error_system_check.html")
        assert is_msit_error_page(html)
        assert parse_msit_detail(html) is None

    def test_normal_page_is_not_error(self):
        assert not is_msit_error_page(read_fixture("detail_3186897.html"))

    def test_page_without_board_returns_none(self):
        assert parse_msit_detail("<html><head><title>사업공고</title></head><body></body></html>") is None


class TestGetDetail:
    @pytest.mark.asyncio
    async def test_fetches_with_requests(self, crawler, monkeypatch):
        calls = []

        def fake_get(url, timeout):
            calls.append(url)
            return fake_response(read_fixture("detail_3186897.html"))

        monkeypatch.setattr(crawler.session, "get", fake_get)

        detail = await crawler.get_detail("3186897")

        assert calls == [CRAWL_SOURCES["msit"]["detail_url_template"].format(id="3186897")]
        assert len(detail["attachments"]) == 2
        assert detail["content"]
        assert crawler.browser is None, "정적 페이지인데 브라우저를 띄웠다"

    @pytest.mark.asyncio
    async def test_error_page_returns_none_with_warning(self, crawler, monkeypatch, caplog):
        monkeypatch.setattr(
            crawler.session, "get", lambda url, timeout: fake_response(read_fixture("error_system_check.html"))
        )

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert await crawler.get_detail("3186897") is None

        assert any("시스템 점검" in r.message for r in caplog.records if r.levelname == "WARNING")

    @pytest.mark.asyncio
    async def test_falls_back_to_browser_when_requests_fails(self, crawler, monkeypatch):
        def broken_get(url, timeout):
            raise requests.ConnectionError("connection reset")

        browser_urls = []

        async def fake_browser_fetch(url):
            browser_urls.append(url)
            return read_fixture("detail_3186897.html")

        monkeypatch.setattr(crawler.session, "get", broken_get)
        monkeypatch.setattr(crawler, "_fetch_html_with_browser", fake_browser_fetch)

        detail = await crawler.get_detail("3186897")

        assert browser_urls == [crawler.detail_url("3186897")]
        assert len(detail["attachments"]) == 2

    @pytest.mark.asyncio
    async def test_404_does_not_retry_with_browser(self, crawler, monkeypatch, caplog):
        async def fail_browser(url):
            raise AssertionError("404인데 브라우저로 재시도했다")

        monkeypatch.setattr(crawler.session, "get", lambda url, timeout: fake_response("", status=404))
        monkeypatch.setattr(crawler, "_fetch_html_with_browser", fail_browser)

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert await crawler.get_detail("1") is None
        assert any(r.levelname == "WARNING" for r in caplog.records)

    @pytest.mark.asyncio
    async def test_works_without_playwright(self, crawler, monkeypatch):
        """상세 조회는 requests만으로 동작한다"""
        monkeypatch.setattr(msit_crawler, "PLAYWRIGHT_AVAILABLE", False)
        monkeypatch.setattr(
            crawler.session, "get", lambda url, timeout: fake_response(read_fixture("detail_3186897.html"))
        )
        assert (await crawler.get_detail("3186897"))["attachments"]


# ---------------------------------------------------------------- 목록 크롤링


class FakeElement:
    def __init__(self, text: str = "", attrs: dict | None = None, children: dict | None = None):
        self._text = text
        self._attrs = attrs or {}
        self._children = children or {}

    async def inner_text(self):
        return self._text

    async def get_attribute(self, name):
        return self._attrs.get(name)

    async def query_selector(self, selector):
        return self._children.get(selector)


class FakePage:
    def __init__(self):
        self.closed = False
        self.url = CRAWL_SOURCES["msit"]["list_url"]

    async def goto(self, url, **kwargs):
        self.url = url

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
            source=Source.MSIT,
            title=f"공고 {page_num}-{i}",
            url=f"https://example.com/{page_num}/{i}",
        )
        for i in range(n)
    ]


@pytest.fixture
def fake_listing(crawler, monkeypatch):
    """페이지당 10건을 돌려주는 가짜 목록 — _go_next_page 호출 횟수를 센다"""
    monkeypatch.setattr(msit_crawler, "PLAYWRIGHT_AVAILABLE", True)
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
        """첫 페이지(10건)로 max_items=5를 채우면 다음 페이지를 불러오지 않는다"""
        state, page = fake_listing

        result = await crawler.crawl(max_items=5)

        assert len(result) == 5
        assert state["next_calls"] == 0
        assert page.closed

    @pytest.mark.asyncio
    async def test_stops_at_max_pages_without_extra_navigation(self, crawler, fake_listing):
        """max_pages=5면 다음 페이지 이동은 4번 (6페이지를 불러오지 않음)"""
        state, _ = fake_listing

        result = await crawler.crawl(max_items=50)

        assert len(result) == 50
        assert state["next_calls"] == 4

    @pytest.mark.asyncio
    async def test_partial_page_need(self, crawler, fake_listing):
        state, _ = fake_listing

        result = await crawler.crawl(max_items=15)

        assert len(result) == 15
        assert state["next_calls"] == 1

    @pytest.mark.asyncio
    async def test_new_page_failure_returns_empty(self, crawler, monkeypatch, caplog):
        """new_page 실패도 크롤러 안에서 처리 (예외 전파 없음) + 0건 WARNING"""
        monkeypatch.setattr(msit_crawler, "PLAYWRIGHT_AVAILABLE", True)

        class DeadBrowser(FakeBrowser):
            async def new_page(self):
                raise Exception("Browser.new_page: Target page, context or browser has been closed")

        async def fake_init_browser():
            crawler.browser = DeadBrowser(None)

        monkeypatch.setattr(crawler, "_init_browser", fake_init_browser)

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert await crawler.crawl(max_items=5) == []
        assert any("0건" in r.message for r in caplog.records if r.levelname == "WARNING")

    @pytest.mark.asyncio
    async def test_keeps_collected_pages_when_later_page_fails(self, crawler, fake_listing, monkeypatch, caplog):
        """2페이지 파싱이 'page is navigating'으로 실패해도 1페이지 결과는 유지 (예전에는 0건 반환)"""
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
    async def test_keeps_collected_pages_when_navigation_raises(self, crawler, fake_listing, monkeypatch):
        state, _ = fake_listing

        async def broken_go_next(_page, current):
            raise RuntimeError("Target page, context or browser has been closed")

        monkeypatch.setattr(crawler, "_go_next_page", broken_go_next)

        assert len(await crawler.crawl(max_items=50)) == 10

    @pytest.mark.asyncio
    async def test_stops_when_page_repeats(self, crawler, fake_listing, monkeypatch):
        """다음 페이지 이동 후에도 같은 목록이면 중단"""
        state, _ = fake_listing

        async def same_page(_page):
            return make_items(1)

        monkeypatch.setattr(crawler, "_parse_page", same_page)

        result = await crawler.crawl(max_items=50)

        assert len(result) == 10
        assert state["next_calls"] == 1


class TestParseItem:
    @pytest.mark.asyncio
    async def test_url_uses_detail_template(self, crawler):
        item = FakeElement(children={
            "p.title": FakeElement("2026년도 한미 이공분야 학생 인력교류 프로그램(NSF IRES) 운영 지원사업 신규과제 공모"),
            "a[onclick*='fn_detail']": FakeElement(attrs={"onclick": "fn_detail(3186897);"}),
            "div.date": FakeElement("Oct 16, 2026"),
            "dd[id^='td_CHRG_DEPT_NM_']": FakeElement("미주아시아협력담당관"),
        })

        ann = await crawler._parse_item(item)

        assert ann.id == "3186897"
        assert ann.url == (
            "https://www.msit.go.kr/bbs/view.do?sCode=user&mPid=121&mId=311"
            "&bbsSeqNo=100&nttSeqNo=3186897"
        )
        assert ann.organization == "미주아시아협력담당관"
        assert ann.posted_date.date().isoformat() == "2026-10-16"
        assert ann.category is None

    @pytest.mark.asyncio
    async def test_item_without_detail_id_is_skipped_not_hashed(self, crawler, caplog):
        """fn_detail(nttSeqNo)가 없으면 제목 해시로 대체하지 않고 건너뜀 (저장 키 체계 변경 방지)"""
        item = FakeElement(children={
            "p.title": FakeElement("ID 없는 공고"),
            "a[onclick*='fn_detail']": FakeElement(attrs={"onclick": "fn_detail();"}),
        })

        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            assert await crawler._parse_item(item) is None
        assert any("게시물 번호" in r.message and "ID 없는 공고" in r.message for r in caplog.records)

        no_link = FakeElement(children={"p.title": FakeElement("링크 없는 공고")})
        assert await crawler._parse_item(no_link) is None
