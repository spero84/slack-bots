"""NipaCrawler 테스트 (실제 NIPA 사업공고 페이지를 축약한 fixture 사용, 네트워크 없음)"""
import logging
import re
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from src.gov_funding.crawlers import nipa_crawler
from src.gov_funding.crawlers.nipa_crawler import NipaCrawler
from src.gov_funding.storage import Source

FIXTURES = Path(__file__).parent / "fixtures" / "nipa"
SUPPORTED_EXTS = ("hwp", "hwpx", "pdf", "docx")


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(text: str = "", status: int = 200) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = text.encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = "https://www.nipa.kr/home/2-2"
    resp.reason = "OK" if status < 400 else "Error"
    return resp


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        result = self.handler(url, params or {})
        return result if isinstance(result, requests.Response) else make_response(result)

    def close(self):
        pass


EMPTY_LIST = '<html><body><table class="tbgg"><tbody></tbody></table></body></html>'


def distinct_page(page: int) -> str:
    """list_p1.html의 공고 ID에 페이지 번호를 붙여 페이지마다 다른 10건을 만든다"""
    return re.sub(r"/home/2-2/(\d+)", lambda m: f"/home/2-2/{page}{m.group(1)}", read_fixture("list_p1.html"))


@pytest.fixture
def crawler(monkeypatch):
    monkeypatch.setattr(nipa_crawler, "today_kst", lambda: date(2026, 10, 20))
    return NipaCrawler(page_delay=0)


def extension(name: str) -> str:
    """bedrock_analyzer._fetch_detail_content와 같은 방식의 확장자 판별"""
    return name.lower().rsplit(".", 1)[-1] if "." in name else ""


class TestParseListPage:
    def test_row_mapping(self, crawler):
        items = crawler.parse_list_page(read_fixture("list_p1.html"))
        assert len(items) == 10
        ann = items[0]

        assert ann.id == "16969"
        assert ann.source == Source.NIPA
        assert ann.title == "2026년 한-두바이 AI 사이버보안 포럼 운영 용역 모집"
        assert ann.category == "해외IT지원센터(UAE) 운영"
        assert ann.deadline == datetime(2026, 11, 4, 22, 0)
        assert ann.d_day == 15
        assert ann.posted_date == datetime(2026, 10, 15)
        assert ann.organization == "NIPA"
        assert ann.url == "https://www.nipa.kr/home/2-2/16969"

    def test_posted_date_on_all_rows(self, crawler):
        items = crawler.parse_list_page(read_fixture("list_p1.html"))
        assert all(isinstance(a.posted_date, datetime) for a in items)

    def test_closed_rows_have_no_d_day(self, crawler):
        items = crawler.parse_list_page(read_fixture("list_p1.html"))
        closed = next(a for a in items if a.id == "16953")  # 신청기간 ~ 2026-10-10, '종료'
        assert closed.deadline == datetime(2026, 10, 10, 23, 0)
        assert closed.d_day is None

    def test_d_day_uses_kst_today(self, crawler, monkeypatch):
        monkeypatch.setattr(nipa_crawler, "today_kst", lambda: date(2026, 11, 4))
        items = crawler.parse_list_page(read_fixture("list_p1.html"))
        assert items[0].d_day == 0

    def test_row_without_id_is_skipped_not_hashed(self, crawler, caplog):
        """href에 /home/2-2/{id}가 없으면 href 해시로 대체하지 않고 건너뜀 (저장 키 체계 변경 방지)"""
        html = read_fixture("list_p1.html").replace('href="/home/2-2/16969"', 'href="/home/2-2/view?no=16969"')
        assert 'href="/home/2-2/view?no=16969"' in html

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            items = crawler.parse_list_page(html)

        assert len(items) == 9
        assert all(a.id.isdigit() for a in items)
        assert "16969" not in {a.id for a in items}
        assert "ID(/home/2-2/{id})를 찾을 수 없어 건너뜀" in caplog.text

    def test_no_table(self, crawler, caplog):
        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            assert crawler.parse_list_page("<html><body></body></html>") == []
        assert "NIPA 테이블을 찾을 수 없음" in caplog.text


class TestCrawl:
    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self, crawler):
        session = FakeSession(
            lambda url, params: read_fixture("list_p1.html") if params["curPage"] == 1 else EMPTY_LIST
        )
        crawler.session = session

        result = await crawler.crawl(max_items=50)

        assert [p["curPage"] for _, p in session.calls] == [1, 2]
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_max_items(self, crawler):
        session = FakeSession(lambda url, params: read_fixture("list_p1.html"))
        crawler.session = session

        result = await crawler.crawl(max_items=5)

        assert len(session.calls) == 1
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_zero_items_warning(self, crawler, caplog):
        crawler.session = FakeSession(lambda url, params: EMPTY_LIST)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert result == []
        assert "NIPA 크롤링 결과 0건" in caplog.text

    @pytest.mark.asyncio
    async def test_http_error_warning(self, crawler, caplog):
        crawler.session = FakeSession(lambda url, params: make_response("", status=403))

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert result == []
        assert "HTTP 403" in caplog.text


    @pytest.mark.asyncio
    async def test_unexpected_error_does_not_raise(self, crawler, caplog):
        def handler(url, params):
            raise ValueError("boom")

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            assert await crawler.crawl() == []
        assert "처리 오류" in caplog.text and "boom" in caplog.text

    @pytest.mark.asyncio
    async def test_stops_at_max_pages(self, monkeypatch):
        """사이트가 계속 새 페이지를 돌려줘도 max_pages까지만 요청"""
        monkeypatch.setattr(nipa_crawler, "today_kst", lambda: date(2026, 10, 20))
        crawler = NipaCrawler(page_delay=0, max_pages=3)
        session = FakeSession(lambda url, params: distinct_page(params["curPage"]))
        crawler.session = session

        result = await crawler.crawl(max_items=500)

        assert [p["curPage"] for _, p in session.calls] == [1, 2, 3]
        assert len(result) == 30

    def test_max_pages_default_from_config(self):
        assert NipaCrawler(page_delay=0).max_pages == nipa_crawler.CRAWL_SOURCES["nipa"]["max_pages"] == 10

    @pytest.mark.asyncio
    async def test_stops_when_page_param_ignored(self, crawler, caplog):
        """curPage가 무시돼 같은 목록이 반복되면 2페이지에서 중단 (예전에는 무한 반복)"""
        session = FakeSession(lambda url, params: read_fixture("list_p1.html"))
        crawler.session = session

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl(max_items=50)

        assert [p["curPage"] for _, p in session.calls] == [1, 2]
        assert len(result) == 10
        assert "모두 이전 페이지와 중복" in caplog.text

    @pytest.mark.asyncio
    async def test_keeps_collected_items_when_later_page_fails(self, crawler, caplog):
        def handler(url, params):
            return distinct_page(1) if params["curPage"] == 1 else make_response("", status=500)

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl(max_items=50)

        assert len(result) == 10
        assert "HTTP 500" in caplog.text and "10건 유지" in caplog.text
        assert "크롤링 결과 0건" not in caplog.text


class TestDetail:
    def test_content_from_body_row(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_16969.html"))
        content = detail["content"]

        assert len(content) > 500
        assert content.startswith("2026년 한-두바이 AI 사이버보안 포럼 운영 용역 모집")
        assert "신청기간 : 2026-10-15 08:00 ~ 2026-11-04 22:00" in content
        assert "용역개요" in content
        # 작성자·작성일·조회수(3369)와 첨부파일 행은 본문에 넣지 않음
        assert content.splitlines()[1:3] == [
            "사업구분 : 해외IT지원센터(UAE) 운영",
            "신청기간 : 2026-10-15 08:00 ~ 2026-11-04 22:00",
        ]
        assert "3369" not in content
        assert "파일크기" not in content

    def test_attachments_without_size_suffix(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_16969.html"))
        attachments = detail["attachments"]

        assert len(attachments) == 2
        assert attachments[0]["name"] == "붙임1. 입찰공고문_2026년 한-두바이 AI 사이버보안 포럼 운영 용역.hwp"
        assert attachments[1]["name"] == "붙임2. 제안요청서_2026년 한-두바이 AI 사이버보안 포럼 운영 용역.hwp"
        for att in attachments:
            assert extension(att["name"]) in SUPPORTED_EXTS
            assert att["url"].startswith("https://www.nipa.kr/comm/getFile?srvcId=BBSTY1")
            assert "fileTy=ATTACH" in att["url"]

    def test_notice_file_sorted_first(self, crawler):
        html = read_fixture("detail_16969.html")
        # 첨부 순서를 뒤집어도 입찰공고문이 맨 앞
        first, second = "붙임1. 입찰공고문", "붙임2. 제안요청서"
        swapped = html.replace(first, "@@TMP@@").replace(second, first).replace("@@TMP@@", second)
        detail = crawler.parse_detail_page(swapped)
        assert "입찰공고문" in detail["attachments"][0]["name"]

    def test_no_detail_table(self, crawler):
        assert crawler.parse_detail_page("<html><body><p>점검중</p></body></html>") is None

    @pytest.mark.asyncio
    async def test_get_detail(self, crawler):
        session = FakeSession(lambda url, params: read_fixture("detail_16969.html"))
        crawler.session = session

        detail = await crawler.get_detail("16969")

        assert [url for url, _ in session.calls] == ["https://www.nipa.kr/home/2-2/16969"]
        assert set(detail) == {"content", "attachments"}
        assert detail["attachments"][0]["name"].endswith(".hwp")

    @pytest.mark.asyncio
    async def test_get_detail_http_error(self, crawler, caplog):
        crawler.session = FakeSession(lambda url, params: make_response("", status=404))

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            assert await crawler.get_detail("1") is None
        assert "HTTP 404" in caplog.text

    @pytest.mark.asyncio
    async def test_get_detail_missing_table_warning(self, crawler, caplog):
        crawler.session = FakeSession(lambda url, params: "<html><body></body></html>")

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            assert await crawler.get_detail("1") is None
        assert "본문을 찾을 수 없음" in caplog.text
