"""KStartupCrawler 테스트 (실제 K-Startup 페이지를 축약한 fixture 사용, 네트워크 없음)"""
import logging
import re
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from src.gov_funding.crawlers import kstartup_crawler
from src.gov_funding.crawlers.kstartup_crawler import KStartupCrawler
from src.gov_funding.storage import Source

FIXTURES = Path(__file__).parent / "fixtures" / "kstartup"
LIST_URL = "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do"
ONGOING_VIEW = "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?schM=view&pbancSn={id}"
DEADLINE_VIEW = "https://www.k-startup.go.kr/web/contents/bizpbanc-deadline.do?schM=view&pbancSn={id}"
AGENCY_TYPES = {"공공", "민간", "지자체", "교육"}


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def with_id_offset(html: str, offset: int) -> str:
    """같은 fixture로 다른 페이지를 흉내 내기 위해 공고 ID(pbancSn)만 바꾼다"""
    return re.sub(r"(?<!\d)(17\d{4})(?!\d)", lambda m: str(int(m.group(1)) + offset), html)


def make_response(text: str = "", status: int = 200) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = text.encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = LIST_URL
    resp.reason = "OK" if status < 400 else "Error"
    return resp


class FakeSession:
    """session.get 대체: (url, params) 기록 후 handler 결과 반환"""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        result = self.handler(url, params or {})
        if isinstance(result, requests.Response):
            return result
        return make_response(result)

    def close(self):
        pass


@pytest.fixture
def today(monkeypatch):
    """fixture의 등록일(2026-10-20)에 맞춰 오늘 날짜 고정"""
    current = {"value": date(2026, 10, 20)}
    monkeypatch.setattr(kstartup_crawler, "today_kst", lambda: current["value"])
    return current


@pytest.fixture
def crawler(today):
    c = KStartupCrawler(page_delay=0)
    yield c


def list_server(pages: dict[tuple[str, int], str]):
    """(탭, 페이지) → HTML. 없는 페이지는 '등록된 데이터가 없습니다.' 페이지"""
    empty = read_fixture("list_empty.html")

    def handler(url, params):
        assert url == LIST_URL
        return pages.get((params["pbancClssCd"], int(params["page"])), empty)

    return handler


def full_site() -> dict[tuple[str, int], str]:
    """PBC010: 11페이지 (1페이지 15건, 이후 4건씩), PBC020: 4페이지 (4건씩)"""
    pages = {("PBC010", 1): read_fixture("list_pbc010_p1.html")}
    p2 = read_fixture("list_pbc010_p2.html")
    for n in range(2, 11):
        pages[("PBC010", n)] = with_id_offset(p2, (n - 2) * 1000)
    pages[("PBC010", 11)] = read_fixture("list_pbc010_p11.html")
    p020 = read_fixture("list_pbc020_p1.html")
    for n in range(1, 5):
        pages[("PBC020", n)] = with_id_offset(p020, -(n - 1) * 1000 - 50000)
    return pages


class TestParseListPage:
    def test_full_page_has_15_items(self, crawler):
        items, last_page = crawler.parse_list_page(read_fixture("list_pbc010_p1.html"))
        assert len(items) == 15
        assert last_page == 11
        assert len({a.id for a in items}) == 15
        assert all(a.source == Source.KSTARTUP for a in items)

    def test_field_mapping_public(self, crawler):
        items, _ = crawler.parse_list_page(read_fixture("list_pbc010_p1.html"))
        ann = next(a for a in items if a.id == "179572")

        assert ann.title == "2026년 스타트업 법률지원사업 울산광역시 법률상담회 참여 기업 모집 안내"
        assert ann.category == "멘토링ㆍ컨설팅ㆍ교육"
        # 기관 유형('공공')이 아니라 주관기관
        assert ann.organization == "창업진흥원"
        assert ann.posted_date == datetime(2026, 10, 20)
        assert ann.deadline == datetime(2026, 11, 2, 23, 59, 59)
        assert ann.d_day == 13
        assert ann.url == ONGOING_VIEW.format(id="179572")

    def test_field_mapping_private_event(self, crawler):
        """type05(행사ㆍ네트워크) 등 모든 분야 플래그를 category로 인식"""
        items, _ = crawler.parse_list_page(read_fixture("list_pbc010_p1.html"))
        ann = next(a for a in items if a.id == "179554")
        assert ann.category == "행사ㆍ네트워크"
        assert ann.organization == "주식회사 내비온"
        assert ann.deadline == datetime(2026, 11, 10, 23, 59, 59)

    def test_agency_type_never_used(self, crawler):
        for name in ("list_pbc010_p1.html", "list_pbc010_p2.html", "list_pbc020_p1.html"):
            items, _ = crawler.parse_list_page(read_fixture(name))
            for ann in items:
                assert ann.organization and ann.organization not in AGENCY_TYPES
                assert ann.category and ann.category not in AGENCY_TYPES
                assert ann.posted_date is not None
                assert ann.deadline is not None

    def test_d_day_from_deadline_kst(self, crawler, today):
        today["value"] = date(2026, 11, 2)
        items, _ = crawler.parse_list_page(read_fixture("list_pbc010_p1.html"))
        assert next(a for a in items if a.id == "179572").d_day == 0

        today["value"] = date(2026, 11, 3)
        items, _ = crawler.parse_list_page(read_fixture("list_pbc010_p1.html"))
        assert next(a for a in items if a.id == "179572").d_day is None

    def test_last_page_without_last_button(self, crawler):
        """10페이지 이하이면 a.last 버튼이 없으므로 페이지 번호 최댓값을 쓴다"""
        _, last_page = crawler.parse_list_page(read_fixture("list_pbc020_p1.html"))
        assert last_page == 4

    def test_empty_page(self, crawler):
        assert crawler.parse_list_page(read_fixture("list_empty.html")) == ([], None)

    def test_missing_container(self, crawler, caplog):
        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            assert crawler.parse_list_page("<html><body></body></html>") == ([], None)
        assert "bizPbancList" in caplog.text


class TestCrawl:
    @pytest.mark.asyncio
    async def test_both_tabs_with_region_filter_until_last_page(self, crawler):
        session = FakeSession(list_server(full_site()))
        crawler.session = session

        result = await crawler.crawl(max_items=500)

        requested = [(p["pbancClssCd"], p["page"]) for _, p in session.calls]
        assert [p for t, p in requested if t == "PBC010"] == list(range(1, 12))
        assert [p for t, p in requested if t == "PBC020"] == [1, 2, 3, 4]
        # 마지막 페이지 이후(12페이지 등)는 요청하지 않음
        assert len(requested) == 15
        assert all(p["suptReginCdList"] == "LC00,LC01,LC08" for _, p in session.calls)

        assert len(result) == 15 + 4 * 9 + 4 + 4 * 4
        assert len({a.id for a in result}) == len(result)

    @pytest.mark.asyncio
    async def test_tabs_interleaved_under_small_cap(self, crawler):
        """max_items가 작아도 두 번째 탭이 빠지지 않도록 페이지 단위로 번갈아 요청"""
        session = FakeSession(list_server(full_site()))
        crawler.session = session

        result = await crawler.crawl(max_items=20)

        assert len(result) == 20
        requested = [(p["pbancClssCd"], p["page"]) for _, p in session.calls]
        assert requested == [("PBC010", 1), ("PBC020", 1), ("PBC010", 2)]
        p020_ids = {a.id for a in crawler.parse_list_page(full_site()[("PBC020", 1)])[0]}
        assert p020_ids & {a.id for a in result}

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self, crawler):
        pages = {
            ("PBC010", 1): read_fixture("list_pbc010_p1.html"),
            ("PBC010", 2): read_fixture("list_pbc010_p2.html"),  # 마지막 페이지 표기는 11이지만
        }
        session = FakeSession(list_server(pages))
        crawler.session = session

        result = await crawler.crawl()

        requested = [(p["pbancClssCd"], p["page"]) for _, p in session.calls]
        assert requested == [("PBC010", 1), ("PBC020", 1), ("PBC010", 2), ("PBC010", 3)]
        assert len(result) == 19

    @pytest.mark.asyncio
    async def test_max_pages_cap(self, today, caplog):
        crawler = KStartupCrawler(page_delay=0, max_pages=2, tabs=["PBC010"])
        session = FakeSession(list_server(full_site()))
        crawler.session = session

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert [p["page"] for _, p in session.calls] == [1, 2]
        assert len(result) == 19
        assert "최대 페이지(2) 도달" in caplog.text

    @pytest.mark.asyncio
    async def test_dedupes_ids_across_tabs(self, crawler):
        same = read_fixture("list_pbc020_p1.html")
        pages = {("PBC010", 1): same, ("PBC020", 1): same}
        crawler.session = FakeSession(list_server(pages))

        result = await crawler.crawl()

        assert len(result) == 4

    @pytest.mark.asyncio
    async def test_region_filter_can_be_disabled(self, today):
        crawler = KStartupCrawler(page_delay=0, region_codes=[])
        session = FakeSession(list_server({}))
        crawler.session = session

        await crawler.crawl()

        assert all("suptReginCdList" not in p for _, p in session.calls)

    @pytest.mark.asyncio
    async def test_zero_items_warning(self, crawler, caplog):
        crawler.session = FakeSession(list_server({}))

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert result == []
        assert "K-Startup 크롤링 결과 0건" in caplog.text
        assert "공공기관 탭 0건" in caplog.text

    @pytest.mark.asyncio
    async def test_http_error_warning_keeps_other_tab(self, crawler, caplog):
        """403 Response는 bool이 False — 상태 코드는 e.response is not None으로 읽어야 한다"""
        pages = {("PBC020", 1): read_fixture("list_pbc020_p1.html")}
        serve = list_server(pages)

        def handler(url, params):
            if params["pbancClssCd"] == "PBC010":
                return make_response("forbidden", status=403)
            return serve(url, params)

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert len(result) == 4
        assert "HTTP 403" in caplog.text

    @pytest.mark.asyncio
    async def test_connection_error_warning(self, crawler, caplog):
        def handler(url, params):
            raise requests.ConnectionError("connection refused")

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert result == []
        assert "요청 실패 (HTTP None)" in caplog.text
        assert "K-Startup 크롤링 결과 0건" in caplog.text

    @pytest.mark.asyncio
    async def test_unexpected_error_does_not_raise(self, crawler, caplog):
        """main.py는 모든 크롤러를 하나의 try로 감싸므로 crawl()은 예외를 던지지 않아야 한다"""
        def handler(url, params):
            raise ValueError("boom")

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            assert await crawler.crawl() == []
        assert "처리 오류: boom" in caplog.text

    @pytest.mark.asyncio
    async def test_page_delay_between_requests(self, today, monkeypatch):
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(kstartup_crawler.asyncio, "sleep", fake_sleep)
        crawler = KStartupCrawler(page_delay=0.7, tabs=["PBC010"])
        crawler.session = FakeSession(list_server({("PBC010", 1): read_fixture("list_pbc010_p1.html")}))

        await crawler.crawl()

        # 요청 2회 (1페이지, 빈 2페이지) → 두 번째 요청 전에만 대기
        assert len(sleeps) == 1
        assert 0 < sleeps[0] <= 0.7


class TestDetail:
    def test_parse_detail_page(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_179535.html"))

        assert detail["fields"]["지역"] == "제주특별자치도"
        assert detail["fields"]["주관기관명"] == "(주)로우파트너스"
        assert detail["fields"]["접수기간"] == "2026-10-15 ~ 2026-10-28 18:00"
        content = detail["content"]
        assert content.startswith("2026년 웰컴 투 팁스 7차 참가기업 모집 (제주권)")
        assert "지역: 제주특별자치도" in content
        assert "창업업력: 3년미만" in content
        assert "[신청방법 및 대상]" in content
        assert "신청대상: ◦ 제주권(제주특별자치도) 소재" in content

    def test_attachments(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_179535.html"))
        attachments = detail["attachments"]

        assert len(attachments) == 3
        assert attachments[0]["name"] == "(붙임) 2026년 웰컴 투 팁스 프로그램 모집공고_제주권.hwp"
        assert all(
            a["url"].startswith("https://www.k-startup.go.kr/afile/fileDownload/") for a in attachments
        )
        assert attachments[0]["url"] == "https://www.k-startup.go.kr/afile/fileDownload/WB1Ln"
        assert attachments[-1]["name"].endswith(".png")

    def test_notice_file_sorted_before_forms(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("deadline_175000.html"))
        names = [a["name"] for a in detail["attachments"]]
        assert names[0] == "[붙임] 2025년도 창업·벤처기업 기술자료 임치 지원 계획(안).pdf"
        assert names.index("[양식] 2025년 창업벤처기업 기술자료 임치 지원사업 신청서 양식 일체.hwp") > 0

    def test_closed_shell_has_no_detail(self, crawler):
        assert crawler.parse_detail_page(read_fixture("detail_closed_shell_175000.html")) is None

    @pytest.mark.asyncio
    async def test_get_detail_open(self, crawler):
        session = FakeSession(lambda url, params: read_fixture("detail_179535.html"))
        crawler.session = session

        detail = await crawler.get_detail("179535")

        assert [url for url, _ in session.calls] == [ONGOING_VIEW.format(id="179535")]
        assert set(detail) >= {"content", "attachments"}
        assert detail["attachments"][0]["name"].endswith(".hwp")

    @pytest.mark.asyncio
    async def test_get_detail_falls_back_to_deadline_page(self, crawler):
        pages = {
            ONGOING_VIEW.format(id="175000"): read_fixture("detail_closed_shell_175000.html"),
            DEADLINE_VIEW.format(id="175000"): read_fixture("deadline_175000.html"),
        }
        session = FakeSession(lambda url, params: pages[url])
        crawler.session = session

        detail = await crawler.get_detail("175000")

        assert [url for url, _ in session.calls] == [
            ONGOING_VIEW.format(id="175000"),
            DEADLINE_VIEW.format(id="175000"),
        ]
        assert detail["fields"]["주관기관명"] == "한국고용정보원"
        assert detail["fields"]["접수기간"] == "2025-09-12 ~ 2025-11-28 23:59"

    @pytest.mark.asyncio
    async def test_get_detail_falls_back_on_http_error(self, crawler, caplog):
        def handler(url, params):
            if "ongoing" in url:
                return make_response("", status=500)
            return read_fixture("deadline_175000.html")

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            detail = await crawler.get_detail("175000")

        assert detail is not None
        assert "HTTP 500" in caplog.text

    @pytest.mark.asyncio
    async def test_get_detail_ignores_foreign_redirect(self, crawler):
        shell = read_fixture("detail_closed_shell_175000.html").replace(
            "/web/contents/bizpbanc-deadline.do", "https://evil.example.com/x"
        )
        session = FakeSession(
            lambda url, params: shell if "ongoing" in url else read_fixture("deadline_175000.html")
        )
        crawler.session = session

        await crawler.get_detail("175000")

        assert session.calls[1][0] == DEADLINE_VIEW.format(id="175000")

    @pytest.mark.asyncio
    async def test_get_detail_not_found(self, crawler, caplog):
        session = FakeSession(lambda url, params: read_fixture("detail_closed_shell_175000.html"))
        crawler.session = session

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            detail = await crawler.get_detail("175000")

        assert detail is None
        assert len(session.calls) == 2
        assert "상세 내용을 찾을 수 없음" in caplog.text


def test_no_playwright_dependency():
    """K-Startup은 requests 기반 — Playwright 브라우저를 띄우지 않는다"""
    source = Path(kstartup_crawler.__file__).read_text(encoding="utf-8")
    assert "playwright" not in source.lower()
    assert not hasattr(KStartupCrawler, "_init_browser")
