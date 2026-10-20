"""NiaCrawler 테스트 (실제 NIA 게시판 페이지를 축약한 fixture 사용, 네트워크 없음)"""
import logging
from datetime import datetime
from pathlib import Path

import pytest
import requests

from src.gov_funding.analyzers import relevance_filter
from src.gov_funding.crawlers.nia_crawler import NiaCrawler
from src.gov_funding.storage import Source
from src.gov_funding.utils import CRAWL_SOURCES

FIXTURES = Path(__file__).parent / "fixtures" / "nia"
VIEW_URL = "https://www.nia.or.kr/site/nia_kor/ex/bbs/View.do?cbIdx={cbIdx}&bcIdx={bcIdx}&parentSeq={parentSeq}"
BID_BOARD = CRAWL_SOURCES["nia"]["boards"][0]
NOTICE_BOARD = CRAWL_SOURCES["nia"]["boards"][1]


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(text: str = "", status: int = 200) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = text.encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = "https://www.nia.or.kr/"
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


EMPTY_LIST = (
    '<html><body><ul class="gnb"><li><a href="/">홈</a></li></ul>'
    '<div class="board_type01"><ul></ul></div></body></html>'
)


def list_server(pages: dict[tuple[str, int], str]):
    """(cbIdx, pageIndex) → HTML"""
    def handler(url, params):
        cb_idx = url.split("cbIdx=")[1].split("&")[0]
        return pages.get((cb_idx, int(params["pageIndex"])), EMPTY_LIST)

    return handler


@pytest.fixture
def crawler():
    return NiaCrawler(page_delay=0)


def parse(crawler, name, board):
    return crawler.parse_list_page(read_fixture(name), board)


class TestConfig:
    def test_board_labels(self):
        assert [b["cbIdx"] for b in CRAWL_SOURCES["nia"]["boards"]] == ["78336", "99835"]
        assert NOTICE_BOARD["name"] == "공지사항"
        assert BID_BOARD["name"] == "입찰공고"


class TestParseListPage:
    def test_organization_is_team_not_person(self, crawler):
        items, _ = parse(crawler, "list_bid_p1.html", BID_BOARD)
        ann = next(a for a in items if a.id == "30071")

        assert ann.organization == "재무관리팀"
        assert ann.department == "한국지능정보사회진흥원"
        assert ann.posted_date == datetime(2026, 10, 15)
        assert ann.category == "입찰공고(재공고)"
        assert ann.title == "지방세 AI 상담 서비스 개인정보 영향평가"
        assert ann.url == VIEW_URL.format(cbIdx="78336", bcIdx="30071", parentSeq="30071")
        assert ann.source == Source.NIA
        assert ann.deadline is None and ann.d_day is None

    def test_all_rows_have_posted_date_and_team(self, crawler):
        from bs4 import BeautifulSoup

        for name, board in (("list_bid_p1.html", BID_BOARD), ("list_notice_p1.html", NOTICE_BOARD)):
            items, _ = parse(crawler, name, board)
            assert items
            soup = BeautifulSoup(read_fixture(name), "lxml")
            persons = {ems[0].get_text(strip=True) for ems in (
                li.select("span.writer em") for li in soup.select("div.board_type01 li")
            ) if ems}
            for ann in items:
                assert ann.posted_date is not None
                # 담당자 이름이 붙은 '권남희재무관리팀' 같은 값이 아니어야 한다
                assert ann.organization != "NIA"
                assert not any(person in ann.organization for person in persons)

        items, _ = parse(crawler, "list_notice_p1.html", NOTICE_BOARD)
        assert next(a for a in items if a.id == "30054").organization == "글로벌아카데미"

    def test_skips_evaluation_results_and_cancellations(self, crawler):
        items, row_count = parse(crawler, "list_bid_p1.html", BID_BOARD)
        ids = {a.id for a in items}

        assert row_count == 10
        assert len(items) == 8
        assert "30075" not in ids  # [평가결과 공개]
        assert "30072" not in ids  # [조달입찰공고](취소공고)
        assert "30071" in ids

    def test_notice_board_default_category(self, crawler):
        items, row_count = parse(crawler, "list_notice_p1.html", NOTICE_BOARD)
        assert row_count == 10
        survey = next(a for a in items if a.id == "30082")
        assert survey.category == "공지사항"
        assert survey.title == "NIA 미션·비전 아이디어 공모 설문 조사 안내"
        assert survey.organization == "기획조정팀"

    def test_pinned_notices_parsed_with_old_dates(self, crawler):
        items, _ = parse(crawler, "list_notice_p1.html", NOTICE_BOARD)
        by_id = {a.id: a for a in items}
        assert by_id["28987"].posted_date == datetime(2026, 1, 22)
        assert by_id["29538"].posted_date == datetime(2026, 6, 15)
        assert by_id["28771"].posted_date == datetime(2025, 11, 24)

    def test_deadline_filter_drops_old_pinned_notice(self, crawler, monkeypatch):
        """posted_date가 채워지면 deadline_filter가 1년 가까이 지난 고정 공지를 걸러낸다"""
        fixed_now = datetime(2026, 10, 20, 12, 0, 0)

        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        monkeypatch.setattr(relevance_filter, "datetime", FixedDatetime, raising=False)
        if hasattr(relevance_filter, "now_kst"):
            monkeypatch.setattr(relevance_filter, "now_kst", lambda: fixed_now)
        monkeypatch.setenv("DEADLINE_MAX_DAYS", "61")

        items, _ = parse(crawler, "list_notice_p1.html", NOTICE_BOARD)
        kept = {a.id for a in relevance_filter.deadline_filter(items)}

        assert "28987" not in kept  # 2026-01-22 'AI기본법 시행 및 하위법령 공개'
        assert "28771" not in kept  # 2025-11-24
        assert "30082" in kept  # 2026-10-20

    def test_old_markup_fallback(self, crawler):
        html = """<div class="board_type01"><ul><li>
          <a href="#view" onclick="doBbsFView('99835','12345','A','12345')" title="[사업공고] 옛 공고-첨부파일 있음">
            [사업공고] 옛 공고 <span class="date">2026.02.09</span><span class="dept">AI정책팀</span>
          </a></li></ul></div>"""
        items, row_count = crawler.parse_list_page(html, NOTICE_BOARD)
        assert row_count == 1
        assert items[0].posted_date == datetime(2026, 2, 9)
        assert items[0].organization == "AI정책팀"
        assert items[0].category == "사업공고"

    def test_navigation_links_not_counted(self, crawler):
        items, row_count = crawler.parse_list_page(EMPTY_LIST, BID_BOARD)
        assert items == [] and row_count == 0


class TestCrawl:
    @pytest.mark.asyncio
    async def test_paginates_both_boards(self, crawler):
        pages = {
            ("78336", 1): read_fixture("list_bid_p1.html"),
            ("99835", 1): read_fixture("list_notice_p1.html"),
            ("99835", 2): read_fixture("list_notice_p2_short.html"),
        }
        session = FakeSession(list_server(pages))
        crawler.session = session

        result = await crawler.crawl(max_items=50)

        requested = [(url.split("cbIdx=")[1], p["pageIndex"]) for url, p in session.calls]
        # 입찰공고 2페이지는 빈 페이지 → 중단, 공지사항 2페이지는 4행(<10) → 중단
        assert requested == [("78336", 1), ("78336", 2), ("99835", 1), ("99835", 2)]
        assert len(result) == 8 + 10 + 4
        assert len({a.id for a in result}) == len(result)

    @pytest.mark.asyncio
    async def test_skipped_rows_do_not_end_pagination(self, crawler):
        """제외된 글 때문에 수집 건수가 10건 미만이어도 행이 10건이면 다음 페이지로 진행"""
        pages = {
            ("78336", 1): read_fixture("list_bid_p1.html"),
            ("78336", 2): read_fixture("list_notice_p2_short.html"),
        }
        session = FakeSession(list_server(pages))
        crawler.session = session

        await crawler.crawl(max_items=50)

        bid_pages = [p["pageIndex"] for url, p in session.calls if "78336" in url]
        assert bid_pages == [1, 2]

    @pytest.mark.asyncio
    async def test_per_board_cap(self, crawler):
        pages = {
            ("78336", 1): read_fixture("list_bid_p1.html"),
            ("99835", 1): read_fixture("list_notice_p1.html"),
        }
        session = FakeSession(list_server(pages))
        crawler.session = session

        result = await crawler.crawl(max_items=10)

        assert len(session.calls) == 2
        assert len(result) == 10
        assert sum(a.url.count("cbIdx=78336") for a in result) == 5

    @pytest.mark.asyncio
    async def test_max_pages_cap(self):
        crawler = NiaCrawler(page_delay=0, max_pages=1)
        session = FakeSession(list_server({
            ("78336", 1): read_fixture("list_bid_p1.html"),
            ("78336", 2): read_fixture("list_bid_p1.html"),
        }))
        crawler.session = session

        await crawler.crawl(max_items=50)

        assert [p["pageIndex"] for url, p in session.calls if "78336" in url] == [1]

    @pytest.mark.asyncio
    async def test_zero_items_warning(self, crawler, caplog):
        crawler.session = FakeSession(list_server({}))

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert result == []
        assert "NIA 입찰공고 크롤링 결과 0건" in caplog.text
        assert "NIA 공지사항 크롤링 결과 0건" in caplog.text
        assert "NIA 크롤링 결과 0건" in caplog.text

    @pytest.mark.asyncio
    async def test_http_error_warning(self, crawler, caplog):
        notice = read_fixture("list_notice_p2_short.html")

        def handler(url, params):
            if "78336" in url:
                return make_response("", status=403)
            return notice

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            result = await crawler.crawl()

        assert len(result) == 4
        assert "HTTP 403" in caplog.text


    @pytest.mark.asyncio
    async def test_unexpected_error_does_not_raise(self, crawler, caplog):
        def handler(url, params):
            raise ValueError("boom")

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            assert await crawler.crawl() == []
        assert "처리 오류: boom" in caplog.text


class TestDetail:
    def test_parse_detail_prefers_con_area(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_30071.html"))
        content = detail["content"]

        assert content.startswith("[입찰공고(재공고)] 지방세 AI 상담 서비스 개인정보 영향평가")
        assert "G2B공고번호 R26BK01725670-000(1차), R26BK01772660-000" in content
        # 작성자/조회수/첨부 영역(헤더 잡음)은 본문에 포함하지 않음
        assert "조회수" not in content
        assert "권남희" not in content
        assert "-다운로드" not in content
        # <br> 경계에서 글자가 붙지 않음
        assert "NIA2026-122호\nG2B공고번호" in content

    def test_attachments_deduped_and_notice_first(self, crawler):
        detail = crawler.parse_detail_page(read_fixture("detail_30071.html"))
        attachments = detail["attachments"]

        assert [a["name"] for a in attachments] == [
            "입찰공고문_NIA2026-122호(재공고).hwp",
            "제안요청서(재공고)_지방세_AI_상담_서비스_개인정보_영향평가.hwp",
        ]
        assert attachments[0]["url"] == (
            "https://www.nia.or.kr/common/board/Download.do?bcIdx=30071&cbIdx=78336&fileNo=2"
        )

    def test_download_suffix_stripped_in_fallback(self, crawler):
        """span.file_nm이 없을 때 'xxx.hwp-다운로드' 링크 이름에서 접미사를 정확히 제거"""
        html = """<div class="detail_type01"><div class="con_area">본문</div>
          <a href="/common/board/Download.do?bcIdx=1&cbIdx=78336&fileNo=1">제안요청서.hwp-다운로드</a>
          <a href="https://www.nia.or.kr/common/board/Download.do?bcIdx=1&cbIdx=78336&fileNo=1">제안요청서.hwp-다운로드</a>
          <a href="javascript:goPreview('1','78336','1');">제안요청서.hwp-미리보기</a>
        </div>"""
        detail = crawler.parse_detail_page(html)
        assert detail["attachments"] == [{
            "name": "제안요청서.hwp",
            "url": "https://www.nia.or.kr/common/board/Download.do?bcIdx=1&cbIdx=78336&fileNo=1",
        }]

    def test_wrong_board_page(self, crawler):
        assert crawler.parse_detail_page(read_fixture("detail_wrong_board.html")) is None

    @pytest.mark.asyncio
    async def test_get_detail_uses_cached_board_first(self, crawler):
        """crawl()에서 본 cbIdx/parentSeq로 바로 조회 (게시판 추측 요청 없음)"""
        crawler.session = FakeSession(list_server({("78336", 1): read_fixture("list_bid_p1.html")}))
        await crawler.crawl(max_items=10)

        session = FakeSession(lambda url, params: read_fixture("detail_30071.html"))
        crawler.session = session
        detail = await crawler.get_detail("30071")

        assert [url for url, _ in session.calls] == [
            VIEW_URL.format(cbIdx="78336", bcIdx="30071", parentSeq="30071")
        ]
        assert detail["attachments"][0]["name"].endswith(".hwp")

    @pytest.mark.asyncio
    async def test_get_detail_cached_notice_board(self, crawler):
        crawler.session = FakeSession(list_server({("99835", 1): read_fixture("list_notice_p1.html")}))
        await crawler.crawl(max_items=50)

        session = FakeSession(lambda url, params: read_fixture("detail_30071.html"))
        crawler.session = session
        await crawler.get_detail("30082")

        assert session.calls[0][0] == VIEW_URL.format(cbIdx="99835", bcIdx="30082", parentSeq="30082")
        assert len(session.calls) == 1

    @pytest.mark.asyncio
    async def test_get_detail_uncached_tries_boards_in_order(self, crawler):
        def handler(url, params):
            if "cbIdx=78336" in url:
                return read_fixture("detail_wrong_board.html")
            return read_fixture("detail_30071.html")

        session = FakeSession(handler)
        crawler.session = session

        detail = await crawler.get_detail("30059")

        assert [url for url, _ in session.calls] == [
            VIEW_URL.format(cbIdx="78336", bcIdx="30059", parentSeq="30059"),
            VIEW_URL.format(cbIdx="99835", bcIdx="30059", parentSeq="30059"),
        ]
        assert detail is not None

    @pytest.mark.asyncio
    async def test_get_detail_not_found(self, crawler, caplog):
        def handler(url, params):
            if "cbIdx=78336" in url:
                return make_response("", status=500)
            return read_fixture("detail_wrong_board.html")

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger="gov-funding-monitor"):
            detail = await crawler.get_detail("99999")

        assert detail is None
        assert "HTTP 500" in caplog.text
        assert "NIA 상세 조회 실패 (99999)" in caplog.text
