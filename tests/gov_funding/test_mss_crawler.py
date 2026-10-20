"""MssCrawler 테스트 (실제 MSS 페이지를 축약한 fixture 사용, 네트워크 없음)

fixture
- view_1071785.html: 신청기간 '~'(빈 값), 본문은 엔티티 이스케이프된 textarea, 첨부 hwpx/pdf
- view_1071194.html: 신청기간 '2026-09-15 ~ 2026-10-06', textarea 안에 <div data-hjsonver> 포함, 첨부 4개
- list_p1.html: 목록 2행 (1071864 신청기간 있음, 1071785 신청기간 없음)
"""
import logging
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from src.gov_funding.crawlers import mss_crawler as mss_module
from src.gov_funding.crawlers.mss_crawler import MssCrawler
from src.gov_funding.storage import Source
from src.gov_funding.utils.deadline_text import parse_deadline_from_text

FIXTURES = Path(__file__).parent / "fixtures" / "mss"
DOWNLOAD_PREFIX = "https://www.mss.go.kr/common/board/Download.do?bcIdx={id}&cbIdx=310&streFileNm="


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(text: str = "", status: int = 200) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = text.encode("utf-8")
    resp.encoding = "utf-8"
    resp.url = "https://www.mss.go.kr/"
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


LOGGER = "gov-funding-monitor"


@pytest.fixture
def crawler():
    return MssCrawler(page_delay=0)


LIST_ROW = (
    "<tr class=\"\" onclick=\"doBbsFView('310','{id}','16010100','{id}');\" title=\"공고 {id}\">"
    "<td>{id}</td>"
    "<td class=\"subject\"><a class=\"pc-detail\" href=\"#view\" title=\"공고 {id}\">공고 {id}</a></td>"
    "<td class=\"attached-files\"></td><td>2026.10.20</td><td>1</td></tr>"
)


def list_page(ids, extra: str = "") -> str:
    """실제 목록과 같은 구조(table > tbody > tr onclick=doBbsFView)의 목록 HTML"""
    rows = "".join(LIST_ROW.format(id=i) for i in ids)
    return f"<html><body>{extra}<div class='board_list'><table><tbody>{rows}</tbody></table></div></body></html>"


def page_ids(page: int, n: int = 10) -> list[str]:
    return [str(1072000 - (page - 1) * n - i) for i in range(n)]


def list_handler(pages: dict):
    """pageIndex → HTML 또는 Response (없는 페이지는 빈 목록)"""
    return lambda url, params: pages.get(params["pageIndex"], list_page([]))


def with_pages(crawler: MssCrawler, pages: dict[str, str]) -> FakeSession:
    """bcIdx → HTML"""
    def handler(url, params):
        bc_idx = url.split("bcIdx=")[1].split("&")[0]
        return pages.get(bc_idx, make_response("not found", 404))

    crawler.session = FakeSession(handler)
    return crawler.session


ARTIFACTS = ("<br", "&nbsp;", "&#39;", "&lt;", "&amp;", "&middot;", "&lsquo;", "<div", "hwpEditorBoardContent")
NAV = ("이전글", "다음글", "전체목록", "바로보기", "내려받기")


def assert_no_attachment_labels(content: str) -> None:
    """첨부파일 영역 라벨/아이콘 텍스트가 없다 (본문 속 '공고문(첨부파일)' 같은 문장은 허용)"""
    assert not any(line.strip() == "첨부파일" for line in content.split("\n"))
    for junk in ("hwp 파일", "PDF 파일", "KB]", "MB]"):
        assert junk not in content, junk


class TestGetDetail:
    @pytest.mark.asyncio
    async def test_textarea_body_is_plain_text(self, crawler):
        session = with_pages(crawler, {"1071785": read_fixture("view_1071785.html")})

        detail = await crawler.get_detail("1071785")

        url, _ = session.calls[0]
        assert "bcIdx=1071785" in url and "parentSeq=0" in url
        content = detail["content"]
        for junk in ARTIFACTS + NAV:
            assert junk not in content, junk
        assert_no_attachment_labels(content)
        assert "'2027년 도약(Jump-Up) 프로그램' 참여기업 모집공고를 첨부와 같이 공고합니다." in content
        assert "도약 프로그램 통합정보관리시스템" in content
        # 이웃 게시글 제목이 섞이지 않는다
        assert "해외규격인증획득지원사업" not in content
        assert "공급망안정화" not in content

    @pytest.mark.asyncio
    async def test_meta_line_once_and_empty_period_omitted(self, crawler):
        with_pages(crawler, {"1071785": read_fixture("view_1071785.html")})

        content = (await crawler.get_detail("1071785"))["content"]

        first_line = content.split("\n")[0]
        assert first_line == "공고번호: 제2026-591호 | 담당부서: 기업구조개선과 | 등록일: 2026.10.15"
        assert content.count("공고번호") == 1  # PC/모바일 중복 제거
        assert "신청기간" not in content  # '~'만 있는 빈 기간은 넣지 않는다 (등록일 오인 방지)
        assert parse_deadline_from_text(content) is None

    @pytest.mark.asyncio
    async def test_attachments_names_and_urls(self, crawler):
        with_pages(crawler, {"1071785": read_fixture("view_1071785.html")})

        attachments = (await crawler.get_detail("1071785"))["attachments"]

        assert attachments == [
            {
                "name": "(공고)2027년_도약(Jump-Up)_프로그램_모집_공고문.hwpx",
                "url": DOWNLOAD_PREFIX.format(id=1071785) + "5ec2eccf-5588-4d6d-9633-e9dd7bbf1358.hwpx",
            },
            {
                "name": "(공고)2027년_도약(Jump-UP)_프로그램_모집_공고문.pdf",
                "url": DOWNLOAD_PREFIX.format(id=1071785) + "616ad874-cee9-458c-8b63-873d955be3cc.pdf",
            },
        ]

    @pytest.mark.asyncio
    async def test_nested_editor_div_and_period(self, crawler):
        with_pages(crawler, {"1071194": read_fixture("view_1071194.html")})

        detail = await crawler.get_detail("1071194")
        content = detail["content"]

        for junk in ARTIFACTS + NAV:
            assert junk not in content, junk
        assert_no_attachment_labels(content)
        assert content.startswith(
            "공고번호: 제2026-556호 | 신청기간: 2026-09-15 ~ 2026-10-06 | "
            "담당부서: 신산업기술창업과 | 등록일: 2026.09.15\n"
        )
        assert "‘공공데이터 활용 지원’ 공공기관 제안형(Top-Down)" in content
        assert "□ 모집기간 : 2026. 9. 15.(화) ~ 10. 6.(화) 16:00까지" in content
        assert "□ 선정규모 : 20개사 내외" in content
        assert parse_deadline_from_text(content) == datetime(2026, 10, 6, 23, 59, 59)

        names = [a["name"] for a in detail["attachments"]]
        assert len(names) == 4
        assert names[0].endswith("창업기업_모집공고.pdf")
        assert names[1].endswith("창업기업_모집공고.hwpx")
        assert names[2:] == ["[별첨_1]_공공기관_과제소개서.pdf", "[별첨_2]_사업_신청_매뉴얼.pdf"]
        assert all("[" not in n or n.startswith("[별첨") for n in names)
        assert all(a["url"].startswith(DOWNLOAD_PREFIX.format(id=1071194)) for a in detail["attachments"])

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, crawler):
        with_pages(crawler, {})
        assert await crawler.get_detail("999") is None


class TestParseDetailPage:
    def test_fallback_without_textarea(self, crawler):
        """textarea가 없는 구 레이아웃: 메타/첨부/이전글·다음글을 빼고 본문만"""
        html = """
        <div class="board_view">
          <h4 id="sj">구 레이아웃 공고</h4>
          <table><caption>사업공고 - 공고번호,신청기간 표</caption><tbody>
            <tr><th>공고번호</th><td>제2026-1호</td><th>신청기간</th><td>2026-10-01 ~ 2026-10-31</td></tr>
            <tr><td class="contents_box">본문 첫 줄&lt;br /&gt;둘째&nbsp;줄<br/>셋째 줄</td></tr>
            <tr><th>첨부파일</th><td class="file_list"><ul><li>
              <span class="name">공고문.hwp <em>[10 KB]</em></span>
              <a class="btn type_down" href="/common/board/Download.do?bcIdx=1&amp;cbIdx=310&amp;streFileNm=a.hwp">내려받기</a>
            </li></ul></td></tr>
          </tbody></table>
          <ul class="prev_next_view"><li><a href="#"><span>이전글</span> 다른 공고</a></li></ul>
          <div class="btn_box"><a href="#list">전체목록</a></div>
        </div>
        """
        detail = crawler.parse_detail_page(html)
        content = detail["content"]
        assert content.split("\n")[0] == "공고번호: 제2026-1호 | 신청기간: 2026-10-01 ~ 2026-10-31"
        assert "본문 첫 줄" in content and "둘째 줄" in content and "셋째 줄" in content
        for junk in ("&lt;", "&nbsp;", "<br", "이전글", "다른 공고", "전체목록", "내려받기", "구 레이아웃 공고"):
            assert junk not in content, junk
        assert content.count("제2026-1호") == 1
        assert detail["attachments"] == [
            {"name": "공고문.hwp", "url": "https://www.mss.go.kr/common/board/Download.do?bcIdx=1&cbIdx=310&streFileNm=a.hwp"}
        ]

    def test_gonggo_attachment_first(self, crawler):
        html = """
        <div class="board_view"><textarea id="editContents">본문</textarea>
        <table><tr><th>첨부파일</th><td class="file_list"><ul>
          <li><span class="name">[별첨] 신청서 양식.hwp <em>[1.2 MB]</em></span>
              <a href="/common/board/Download.do?bcIdx=1&amp;streFileNm=form.hwp">내려받기</a></li>
          <li><span class="name">사업 공고문.pdf <em>[842.51 KB]</em></span>
              <a href="/common/board/Download.do?bcIdx=1&amp;streFileNm=notice.pdf">내려받기</a></li>
        </ul></td></tr></table></div>
        """
        names = [a["name"] for a in crawler.parse_detail_page(html)["attachments"]]
        assert names == ["사업 공고문.pdf", "[별첨] 신청서 양식.hwp"]

    def test_open_ended_period_does_not_become_deadline(self, crawler):
        """'2026-07-27 ~' (종료일 없음) + 등록일이 있어도 마감일로 오인되지 않는다"""
        html = """
        <div class="board_view"><table><tbody>
          <tr class="web"><th>공고번호</th><td>제2026-400호</td><th>신청기간</th><td>2026-07-27 ~ </td>
              <th>담당부서</th><td>기술혁신정책과</td><th>등록일</th><td>2026.07.27</td></tr>
          <tr class="m-block"><td><textarea id="editContents">예산 소진 시까지 상시 접수합니다.</textarea></td></tr>
        </tbody></table></div>
        """
        content = crawler.parse_detail_page(html)["content"]
        assert "신청 시작일: 2026-07-27 (종료일 미기재)" in content
        assert parse_deadline_from_text(content) is None

        # 날짜가 아닌 값('상시')도 신청기간 키워드로 쓰지 않는다
        content = crawler.parse_detail_page(html.replace("2026-07-27 ~ ", "상시"))["content"]
        assert "신청 일정: 상시" in content
        assert parse_deadline_from_text(content) is None

    @pytest.mark.parametrize(
        "value, expected",
        [
            (" ~ ", None),
            ("", None),
            ("2026-09-15 ~ 2026-10-06", "신청기간: 2026-09-15 ~ 2026-10-06"),
            ("2026-07-27 ~", "신청 시작일: 2026-07-27 (종료일 미기재)"),
            ("~ 2026-11-01", "신청기간: ~ 2026-11-01"),
            ("2026.11.30", "신청기간: 2026.11.30"),
            ("상시", "신청 일정: 상시"),
        ],
    )
    def test_format_period(self, value, expected):
        assert MssCrawler._format_period(value) == expected


class TestListDday:
    def _crawl(self, crawler, monkeypatch, today: date):
        monkeypatch.setattr(mss_module, "today_kst", lambda: today)
        crawler.session = FakeSession(lambda url, params: read_fixture("list_p1.html"))
        return {a.id: a for a in crawler._crawl_page(1)}

    def test_d_day_uses_kst_today(self, crawler, monkeypatch):
        items = self._crawl(crawler, monkeypatch, date(2026, 10, 20))

        assert set(items) == {"1071864", "1071785"}
        with_period = items["1071864"]
        assert with_period.source == Source.MSS
        assert with_period.title == "2027년도 전통시장 및 상점가 활성화 지원사업 공고"
        assert with_period.deadline == datetime(2026, 11, 16, 23, 59, 59)
        assert with_period.d_day == 27
        assert with_period.posted_date == datetime(2026, 10, 20)

        no_period = items["1071785"]
        assert no_period.deadline is None and no_period.d_day is None

    def test_d_day_zero_on_deadline_day_and_none_after(self, crawler, monkeypatch):
        assert self._crawl(crawler, monkeypatch, date(2026, 11, 16))["1071864"].d_day == 0
        assert self._crawl(crawler, monkeypatch, date(2026, 11, 17))["1071864"].d_day is None


class TestCrawl:
    @pytest.mark.asyncio
    async def test_paginates_until_max_items(self, crawler):
        session = FakeSession(list_handler({n: list_page(page_ids(n)) for n in range(1, 11)}))
        crawler.session = session

        result = await crawler.crawl(max_items=25)

        assert [p["pageIndex"] for _, p in session.calls] == [1, 2, 3]
        assert len(result) == 25
        assert result[0].id == "1072000"
        assert result[0].url.endswith("bcIdx=1072000&parentSeq=1072000")

    @pytest.mark.asyncio
    async def test_stops_at_max_pages(self):
        crawler = MssCrawler(page_delay=0, max_pages=3)
        session = FakeSession(list_handler({n: list_page(page_ids(n)) for n in range(1, 20)}))
        crawler.session = session

        result = await crawler.crawl(max_items=500)

        assert [p["pageIndex"] for _, p in session.calls] == [1, 2, 3]
        assert len(result) == 30

    def test_defaults_from_config(self):
        crawler = MssCrawler()
        assert crawler.max_pages == mss_module.CRAWL_SOURCES["mss"]["max_pages"] == 10
        assert crawler.page_delay == mss_module.CRAWL_SOURCES["mss"]["page_delay"] > 0

    @pytest.mark.asyncio
    async def test_stops_when_page_param_ignored(self, crawler, caplog):
        """pageIndex가 무시돼 같은 10건이 반복되면 2페이지에서 중단 (예전에는 sleep 없이 무한 반복)"""
        session = FakeSession(lambda url, params: list_page(page_ids(1)))
        crawler.session = session

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl(max_items=50)

        assert [p["pageIndex"] for _, p in session.calls] == [1, 2]
        assert len(result) == 10
        assert "모두 이전 페이지와 중복" in caplog.text

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self, crawler):
        session = FakeSession(list_handler({1: read_fixture("list_p1.html")}))
        crawler.session = session

        result = await crawler.crawl(max_items=50)

        assert [p["pageIndex"] for _, p in session.calls] == [1, 2]
        assert [a.id for a in result] == ["1071864", "1071785"]

    @pytest.mark.asyncio
    async def test_sleeps_between_pages(self, monkeypatch):
        """페이지 사이에 page_delay만큼 대기 (첫 페이지 전에는 대기하지 않음)"""
        sleeps = []

        async def fake_sleep(delay, *args, **kwargs):
            sleeps.append(delay)

        monkeypatch.setattr(mss_module.asyncio, "sleep", fake_sleep)
        crawler = MssCrawler(page_delay=1.5)
        crawler.session = FakeSession(list_handler({n: list_page(page_ids(n)) for n in range(1, 4)}))

        await crawler.crawl(max_items=30)

        assert sleeps == [1.5, 1.5]

    @pytest.mark.asyncio
    async def test_zero_items_warning(self, crawler, caplog):
        crawler.session = FakeSession(lambda url, params: list_page([]))

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert "MSS 크롤링 결과 0건" in caplog.text

    @pytest.mark.asyncio
    async def test_zero_items_warning_on_first_page_error(self, crawler, caplog):
        crawler.session = FakeSession(lambda url, params: make_response("blocked", 403))

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert "HTTP 403" in caplog.text
        assert "MSS 크롤링 결과 0건" in caplog.text

    @pytest.mark.asyncio
    async def test_keeps_collected_items_when_later_page_fails(self, crawler, caplog):
        crawler.session = FakeSession(list_handler({
            1: list_page(page_ids(1)),
            2: make_response("Bad Gateway", 502),
        }))

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl(max_items=50)

        assert [a.id for a in result] == page_ids(1)
        assert "HTTP 502" in caplog.text and "10건 유지" in caplog.text
        assert "크롤링 결과 0건" not in caplog.text

    @pytest.mark.asyncio
    async def test_keeps_collected_items_on_unexpected_error(self, crawler, caplog):
        def handler(url, params):
            if params["pageIndex"] == 1:
                return list_page(page_ids(1))
            raise ValueError("boom")

        crawler.session = FakeSession(handler)

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl(max_items=50)

        assert len(result) == 10
        assert "처리 오류" in caplog.text and "boom" in caplog.text


class TestParseListPage:
    def test_row_without_bcidx_is_skipped_not_hashed(self, crawler, caplog):
        """bcIdx가 없으면 제목 해시로 대체하지 않는다 (2026-02 mss_<hex> → mss_<bcIdx> 재알림)"""
        no_id_row = (
            "<tr><td>9999</td><td class='subject'><a href='#view'>ID 없는 공고</a></td>"
            "<td></td><td>2026.10.20</td><td>1</td></tr>"
        )
        html = list_page(["1071999"]).replace("</tbody>", no_id_row + "</tbody>")

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            items = crawler.parse_list_page(html)

        assert [a.id for a in items] == ["1071999"]
        assert "bcIdx" in caplog.text and "ID 없는 공고" in caplog.text

    def test_bcidx_from_link_href_fallback(self, crawler):
        html = (
            "<table><tbody><tr><td>1</td>"
            "<td class='subject'><a href='/site/smba/ex/bbs/View.do?cbIdx=310&amp;bcIdx=1071111'>링크 공고</a></td>"
            "<td></td><td>2026.10.20</td></tr></tbody></table>"
        )
        assert [a.id for a in crawler.parse_list_page(html)] == ["1071111"]

    def test_stray_ul_li_does_not_suppress_table(self, crawler):
        """사이드 메뉴 등 'ul li'에 doBbsFView 링크가 있어도 목록 테이블을 파싱한다"""
        stray = (
            "<ul class='popular'><li><a href='#view' onclick=\"doBbsFView('310','1000001','0','0')\" "
            "title='인기 게시물'>인기 게시물</a></li></ul>"
        )
        items = crawler.parse_list_page(list_page(page_ids(1), extra=stray))

        assert [a.id for a in items] == page_ids(1)

    def test_li_layout_fallback_when_no_table(self, crawler):
        html = (
            "<ul class='board'><li><a href='#view' onclick=\"doBbsFView('310','1071222','0','0')\" "
            "title='[공고] li 구조 공고-첨부파일 있음'>li 구조 공고<span class='date'>2026.10.20</span></a></li></ul>"
        )
        items = crawler.parse_list_page(html)

        assert [(a.id, a.title, a.category) for a in items] == [("1071222", "li 구조 공고", "공고")]
