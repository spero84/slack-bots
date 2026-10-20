"""BizinfoCrawler 테스트 (실제 기업마당 페이지를 축약한 fixture 사용)"""
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.gov_funding.crawlers import bizinfo_crawler
from src.gov_funding.crawlers.bizinfo_crawler import (
    BizinfoCrawler,
    calculate_d_day,
    parse_deadline,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bizinfo"
DETAIL_URL = "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId={id}"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fake_response(text: str, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.status_code = status
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"{status} Error")
    return resp


@pytest.fixture
def crawler(monkeypatch):
    # fixture 페이지의 등록일(2026-10-16~20)에 맞춰 오늘 날짜 고정
    monkeypatch.setattr(bizinfo_crawler, "today_kst", lambda: date(2026, 10, 20))
    c = BizinfoCrawler(lookback_days=3, max_pages=10, page_delay=0)
    yield c


class TestParseDeadline:
    @pytest.mark.parametrize("text,expected", [
        ("2026-10-16 ~ 2026-10-26", datetime(2026, 10, 26, 23, 59, 59)),
        ("2026.10.29 ~ 2026.11.05", datetime(2026, 11, 5, 23, 59, 59)),
        ("20220727 ~ 20220930", datetime(2022, 9, 30, 23, 59, 59)),
        ("~ 2026-10-30", datetime(2026, 10, 30, 23, 59, 59)),
        ("2026-10-30", datetime(2026, 10, 30, 23, 59, 59)),
    ])
    def test_range_uses_end_date(self, text, expected):
        """신청기간이 범위면 시작일이 아닌 종료일을 마감일로 사용"""
        assert parse_deadline(text) == expected

    @pytest.mark.parametrize("text", [
        "예산 소진시까지", "상시 접수", "모집 완료시까지", "회차별 상이", "", "2026-10-01 ~",
    ])
    def test_no_deadline(self, text):
        assert parse_deadline(text) is None


class TestCalculateDDay:
    def test_future(self):
        assert calculate_d_day(datetime(2026, 10, 26, 23, 59, 59), date(2026, 10, 20)) == 6

    def test_today(self):
        assert calculate_d_day(datetime(2026, 10, 20, 23, 59, 59), date(2026, 10, 20)) == 0

    def test_past(self):
        assert calculate_d_day(datetime(2026, 10, 19, 23, 59, 59), date(2026, 10, 20)) is None

    def test_none(self):
        assert calculate_d_day(None) is None


class TestParseListPage:
    def test_row_mapping(self, crawler):
        items, last_page = crawler.parse_list_page(read_fixture("list_variants.html"))
        first = items[0]

        assert first.id == "PBLN_000000000127352"
        assert first.title == "[충남] 홍성군 2026년 (예비)사회적기업 시설장비 지원사업 공고"
        assert first.category == "인력"
        assert first.deadline == datetime(2026, 10, 26, 23, 59, 59)
        assert first.d_day == 6
        assert first.posted_date == datetime(2026, 10, 20)
        assert first.department == "충청남도"
        # 사업수행기관이 '기초자치단체'면 소관부처·지자체로 대체
        assert first.organization == "충청남도"
        assert first.url == DETAIL_URL.format(id="PBLN_000000000127352")

    def test_real_organization_kept(self, crawler):
        items, _ = crawler.parse_list_page(read_fixture("list_variants.html"))
        trademark = next(a for a in items if a.id == "PBLN_000000000127351")
        assert trademark.department == "지식재산처"
        assert trademark.organization == "한국지식재산보호원"

    def test_open_ended_period(self, crawler):
        items, _ = crawler.parse_list_page(read_fixture("list_variants.html"))
        budget = next(a for a in items if a.id == "PBLN_000000000127346")
        assert budget.deadline is None
        assert budget.d_day is None

    def test_id_independent_of_page(self, crawler):
        """href의 cpage 등 검색 상태가 달라도 같은 pblancId면 같은 ID"""
        html = read_fixture("list_variants.html").replace("cpage=1&amp;", "cpage=7&amp;")
        items, _ = crawler.parse_list_page(html)
        assert items[0].id == "PBLN_000000000127352"

    def test_last_page(self, crawler):
        _, last_page = crawler.parse_list_page(read_fixture("list_p1.html"))
        assert last_page == 98

    def test_full_page_has_15_rows(self, crawler):
        items, _ = crawler.parse_list_page(read_fixture("list_p1.html"))
        assert len(items) == 15
        assert all(a.id.startswith("PBLN_") for a in items)

    def test_no_table(self, crawler):
        assert crawler.parse_list_page("<html><body></body></html>") == ([], None)


class TestCrawl:
    def _serve_pages(self, crawler, pages: dict[int, str]):
        requested = []

        def fake_get(url, params=None, timeout=None):
            requested.append(params["cpage"])
            return fake_response(pages.get(params["cpage"], "<table><tbody></tbody></table>"))

        crawler.session.get = fake_get
        return requested

    @pytest.mark.asyncio
    async def test_paginates_until_lookback_cutoff(self, crawler):
        """15건 미만이 아니어도 다음 페이지로 진행하고, 등록일 기준일 이전이 나오면 중단"""
        pages = {n: read_fixture(f"list_p{n}.html") for n in range(1, 5)}
        requested = self._serve_pages(crawler, pages)

        result = await crawler.crawl(max_items=300)

        # page 4에 2026-10-16 등록 공고가 있어 기준일(10-17) 도달 → page 5는 요청하지 않음
        assert requested == [1, 2, 3, 4]
        assert len(result) > 45
        assert all(a.posted_date.date() >= date(2026, 10, 17) for a in result)
        assert len({a.id for a in result}) == len(result)

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self, crawler):
        requested = self._serve_pages(crawler, {1: read_fixture("list_p1.html")})
        crawler.lookback_days = 30

        result = await crawler.crawl()

        assert requested == [1, 2]
        assert len(result) == 15

    @pytest.mark.asyncio
    async def test_stops_at_max_pages(self, crawler):
        page1 = read_fixture("list_p1.html")
        requested = self._serve_pages(crawler, {n: page1 for n in range(1, 10)})
        crawler.lookback_days = 30
        crawler.max_pages = 3

        result = await crawler.crawl()

        assert requested == [1, 2, 3]
        assert len(result) == 15  # 같은 페이지 반복 → ID 기준 중복 제거

    @pytest.mark.asyncio
    async def test_respects_max_items(self, crawler):
        pages = {n: read_fixture(f"list_p{n}.html") for n in range(1, 5)}
        requested = self._serve_pages(crawler, pages)

        result = await crawler.crawl(max_items=20)

        assert len(result) == 20
        assert requested == [1, 2]

    @pytest.mark.asyncio
    async def test_http_error_returns_partial(self, crawler, caplog):
        def fake_get(url, params=None, timeout=None):
            if params["cpage"] == 1:
                return fake_response(read_fixture("list_p1.html"))
            return fake_response("", status=500)

        crawler.session.get = fake_get
        crawler.lookback_days = 30

        result = await crawler.crawl()

        assert len(result) == 15

    @pytest.mark.asyncio
    async def test_zero_items_warns(self, crawler, caplog):
        self._serve_pages(crawler, {})
        with caplog.at_level("WARNING", logger="gov-funding-monitor"):
            result = await crawler.crawl()
        assert result == []
        assert any("0건" in r.message for r in caplog.records)


class TestDetail:
    def test_fields_and_content(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_127266.html"))

        assert "[사업개요]" in detail["content"]
        assert "[문의처]" in detail["content"]
        assert "사업개요" in detail["fields"]

    def test_attachments_notice_first(self, crawler):
        """'공고'가 들어간 첨부파일을 신청서/포스터보다 앞에 둔다"""
        detail = crawler.parse_detail_page(read_fixture("detail_127266.html"))
        attachments = detail["attachments"]

        assert len(attachments) == 3
        assert "공고" in attachments[0]["name"]
        assert attachments[0]["url"] == (
            "https://www.bizinfo.go.kr/cmm/fms/fileDown.do?atchFileId=FILE_000000000776874&fileSn=0"
        )

    def test_body_output_file_first(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_127338.html"))
        first = detail["attachments"][0]
        assert first["kind"] == "본문출력파일"
        assert first["name"].endswith(".hwp")

    def test_mixed_headings_in_one_list(self, crawler):
        """같은 목록 안에 '첨부파일'(zip)과 '본문출력파일'(pdf)이 섞인 경우 pdf 공고문이 먼저"""
        detail = crawler.parse_detail_page(read_fixture("detail_127227.html"))
        names = [a["name"] for a in detail["attachments"]]

        assert names[0].endswith(".pdf")
        assert detail["attachments"][0]["kind"] == "본문출력파일"
        assert names[-1].endswith(".zip")
        assert "[신청기간] 예산 소진시까지" in detail["content"]

    def test_not_a_detail_page(self, crawler):
        assert crawler.parse_detail_page("<html><body>requested page not found</body></html>") is None

    @pytest.mark.asyncio
    async def test_get_detail_uses_canonical_url(self, crawler):
        calls = []

        def fake_get(url, timeout=None):
            calls.append(url)
            return fake_response(read_fixture("detail_127266.html"))

        crawler.session.get = fake_get
        detail = await crawler.get_detail("PBLN_000000000127266")

        assert calls == [DETAIL_URL.format(id="PBLN_000000000127266")]
        assert detail["attachments"]

    @pytest.mark.asyncio
    async def test_get_detail_404(self, crawler):
        crawler.session.get = lambda url, timeout=None: fake_response("not found", status=404)
        assert await crawler.get_detail("PBLN_000000000000000") is None
