"""IitpCrawler 테스트 (IRIS 사업공고 실제 응답을 축약한 fixture 사용, 네트워크 없음)"""
import copy
import json
import logging
from datetime import date, datetime
from http.client import RemoteDisconnected
from pathlib import Path

import pytest
import requests

from src.gov_funding.crawlers import iitp_crawler
from src.gov_funding.crawlers.iitp_crawler import IitpCrawler, parse_date
from src.gov_funding.storage import Source

FIXTURES = Path(__file__).parent / "fixtures" / "iitp"
LIST_API = "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do"
VIEW_URL = "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do"
DOWNLOAD_URL = "https://www.iris.go.kr/comm/file/fileDownload.do"
LOGGER = "gov-funding-monitor"


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def read_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(body, status: int = 200, content_type: str = "application/json;charset=UTF-8") -> requests.Response:
    """실제 requests.Response (4xx/5xx에서 bool()이 False인 동작까지 동일)"""
    resp = requests.Response()
    resp.status_code = status
    resp.url = LIST_API
    resp.reason = "Error" if status >= 400 else "OK"
    resp.headers["content-type"] = content_type
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    resp._content = body.encode("utf-8") if isinstance(body, str) else body
    return resp


def empty_list(prg: str) -> dict:
    data = load_json("iris_list_ancmIng_empty.json")
    data["ancmPrg"] = prg
    return data


def remote_disconnected() -> requests.exceptions.ConnectionError:
    return requests.exceptions.ConnectionError(
        ("Connection aborted.", RemoteDisconnected("Remote end closed connection without response"))
    )


class FakePost:
    """(ancmPrg, pageIndex)별 응답 시퀀스를 돌려주는 session.post 대체"""

    def __init__(self, routes: dict):
        self.routes = {k: list(v) if isinstance(v, list) else [v] for k, v in routes.items()}
        self.calls = []

    def __call__(self, url, data=None, headers=None, timeout=None):
        key = (data["ancmPrg"], data["pageIndex"])
        self.calls.append({"url": url, "data": dict(data), "headers": dict(headers or {}), "timeout": timeout})
        seq = self.routes.get(key)
        if not seq:
            raise AssertionError(f"예상치 못한 요청: {key}")
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, requests.Response):
            return item
        return make_response(item)

    @property
    def keys(self):
        return [(c["data"]["ancmPrg"], c["data"]["pageIndex"]) for c in self.calls]


@pytest.fixture
def sleeps(monkeypatch):
    """asyncio.sleep을 즉시 반환하도록 대체하고 대기 시간 기록"""
    recorded = []

    async def fake_sleep(delay, *args, **kwargs):
        recorded.append(delay)

    monkeypatch.setattr(iitp_crawler.asyncio, "sleep", fake_sleep)
    return recorded


@pytest.fixture
def crawler(monkeypatch, sleeps):
    # fixture 응답 수집일(2026-10-20 KST) 기준
    monkeypatch.setattr(iitp_crawler, "today_kst", lambda: date(2026, 10, 20))
    c = IitpCrawler(retry_delays=(10, 60), request_delay=0)
    yield c


def warnings_of(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == LOGGER]


class TestParseDate:
    @pytest.mark.parametrize("text,end_of_day,expected", [
        ("2026.11.16", True, datetime(2026, 11, 16, 23, 59, 59)),
        ("2026-10-15", False, datetime(2026, 10, 15)),
        ("2026.11.3", True, datetime(2026, 11, 3, 23, 59, 59)),
    ])
    def test_formats(self, text, end_of_day, expected):
        assert parse_date(text, end_of_day=end_of_day) == expected

    @pytest.mark.parametrize("text", [None, "", "미정", "2026.13.40"])
    def test_invalid(self, text):
        assert parse_date(text) is None


class TestNoPlaywright:
    def test_module_does_not_use_playwright(self):
        source = Path(iitp_crawler.__file__).read_text(encoding="utf-8")
        assert "playwright" not in source.lower()
        assert not hasattr(IitpCrawler, "_init_browser")


class TestCrawlMapping:
    @pytest.mark.asyncio
    async def test_upcoming_item_mapping(self, crawler):
        crawler.session.post = FakePost({
            ("ancmIng", "1"): empty_list("ancmIng"),
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })

        result = await crawler.crawl(max_items=50)

        assert len(result) == 8
        first = result[0]
        assert first.id == "024439"
        assert first.source == Source.IITP
        assert first.title == "2026년도 AI 기반 주파수 간섭분석 및 전파예측기술 개발사업 신규지원 대상과제 공고"
        assert first.deadline == datetime(2026, 11, 16, 23, 59, 59)
        assert first.d_day == 27
        assert first.posted_date == datetime(2026, 10, 15)
        assert first.department == "과학기술정보통신부"
        assert first.organization == "정보통신기획평가원"
        assert first.category == "사업공고(접수예정)"
        assert first.url == f"{VIEW_URL}?ancmId=024439&ancmPrg=ancmPre"

    @pytest.mark.asyncio
    async def test_null_receipt_dates(self, crawler):
        """접수기간이 없는 오래된 접수예정 공고: 마감일 None, 공고일은 유지 (deadline_filter가 공고일로 제외)"""
        crawler.session.post = FakePost({
            ("ancmIng", "1"): empty_list("ancmIng"),
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })

        result = await crawler.crawl()
        old = next(a for a in result if a.id == "014078")

        assert old.deadline is None
        assert old.d_day is None
        assert old.posted_date == datetime(2025, 6, 17)
        # blngGovdSeNm이 null이면 기본 소관부처
        assert old.department == "과학기술정보통신부"
        assert all(a.posted_date is not None for a in result)

    @pytest.mark.asyncio
    async def test_request_form_and_headers(self, crawler):
        fake = FakePost({
            ("ancmIng", "1"): empty_list("ancmIng"),
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })
        crawler.session.post = fake

        await crawler.crawl()

        assert fake.keys == [("ancmIng", "1"), ("ancmPre", "1")]
        for call in fake.calls:
            assert call["url"] == LIST_API
            assert call["data"]["sorgnId"] == "10004"
            assert call["data"]["sorgnIdArr"] == "10004"
            assert call["headers"]["X-Requested-With"] == "XMLHttpRequest"
            assert call["headers"]["Referer"] == "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do"
            assert call["timeout"]

    @pytest.mark.asyncio
    async def test_open_items_first_and_deduped(self, crawler):
        """접수중 → 접수예정 순서, 같은 ancmId는 한 번만"""
        ing = load_json("iris_list_ancmEnd_p1.json")
        ing["ancmPrg"] = "ancmIng"
        ing["paginationInfo"]["totalPageCount"] = 1
        pre = load_json("iris_list_ancmPre.json")
        pre["listBsnsAncmBtinSitu"].insert(0, copy.deepcopy(ing["listBsnsAncmBtinSitu"][0]))
        crawler.session.post = FakePost({("ancmIng", "1"): ing, ("ancmPre", "1"): pre})

        result = await crawler.crawl()

        ids = [a.id for a in result]
        assert ids[:4] == ["024258", "023897", "023817", "024439"]
        assert len(ids) == len(set(ids)) == 3 + 8
        assert result[0].category == "사업공고(접수중)"
        assert result[0].url == f"{VIEW_URL}?ancmId=024258&ancmPrg=ancmIng"
        # 2026-10-08 마감 → 이미 지남
        assert result[1].deadline == datetime(2026, 10, 8, 23, 59, 59)
        assert result[1].d_day is None

    @pytest.mark.asyncio
    async def test_paginates_by_total_page_count(self, crawler):
        p1 = load_json("iris_list_ancmEnd_p1.json")
        p1["ancmPrg"] = "ancmIng"
        p1["paginationInfo"]["totalPageCount"] = 2
        p2 = load_json("iris_list_ancmPre.json")
        p2["ancmPrg"] = "ancmIng"
        p2["paginationInfo"].update({"currentPageNo": 2, "totalPageCount": 2})
        fake = FakePost({
            ("ancmIng", "1"): p1,
            ("ancmIng", "2"): p2,
            ("ancmPre", "1"): empty_list("ancmPre"),
        })
        crawler.session.post = fake

        result = await crawler.crawl()

        assert fake.keys == [("ancmIng", "1"), ("ancmIng", "2"), ("ancmPre", "1")]
        assert len(result) == 3 + 8

    @pytest.mark.asyncio
    async def test_max_pages_cap(self, crawler):
        """totalPageCount가 커도 상태별 max_pages까지만 요청"""
        big = load_json("iris_list_ancmEnd_p1.json")  # 실제 totalPageCount=35
        assert big["paginationInfo"]["totalPageCount"] == 35
        routes = {("ancmIng", str(n)): big for n in range(1, 40)}
        routes[("ancmPre", "1")] = empty_list("ancmPre")
        fake = FakePost(routes)
        crawler.session.post = fake

        await crawler.crawl(max_items=500)

        ing_pages = [k for k in fake.keys if k[0] == "ancmIng"]
        assert len(ing_pages) == crawler.config["max_pages"]

    @pytest.mark.asyncio
    async def test_max_items_stops_requests(self, crawler):
        ing = load_json("iris_list_ancmEnd_p1.json")
        ing["paginationInfo"]["totalPageCount"] = 1
        fake = FakePost({("ancmIng", "1"): ing})
        crawler.session.post = fake

        result = await crawler.crawl(max_items=2)

        assert [a.id for a in result] == ["024258", "023897"]
        assert fake.keys == [("ancmIng", "1")]

    @pytest.mark.asyncio
    async def test_request_delay_between_requests(self, monkeypatch, sleeps):
        monkeypatch.setattr(iitp_crawler, "today_kst", lambda: date(2026, 10, 20))
        c = IitpCrawler(retry_delays=(0, 0), request_delay=1.5)
        c.session.post = FakePost({
            ("ancmIng", "1"): empty_list("ancmIng"),
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })

        await c.crawl()

        # 첫 요청 전에는 대기하지 않음
        assert sleeps == [1.5]

    @pytest.mark.asyncio
    async def test_malformed_row_is_skipped(self, crawler, caplog):
        """형식이 이상한 항목 하나(문자열 필드에 숫자)는 WARNING 후 건너뛰고 나머지는 수집"""
        pre = load_json("iris_list_ancmPre.json")
        pre["listBsnsAncmBtinSitu"][1]["sorgnNm"] = 10004
        pre["listBsnsAncmBtinSitu"][2]["ancmTl"] = 12345
        pre["listBsnsAncmBtinSitu"].insert(0, "not-a-dict")
        crawler.session.post = FakePost({
            ("ancmIng", "1"): empty_list("ancmIng"),
            ("ancmPre", "1"): pre,
        })

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        ids = [a.id for a in result]
        assert ids == ["024439", "002476", "001651", "II2300", "II2200", "II1800"]
        msgs = warnings_of(caplog)
        assert any("항목 파싱 오류" in m and "ancmId=014078" in m for m in msgs)
        assert any("항목 파싱 오류" in m and "ancmId=013358" in m for m in msgs)


class TestRetry:
    @pytest.mark.asyncio
    async def test_retries_connection_errors_with_backoff(self, crawler, sleeps, caplog):
        fake = FakePost({
            ("ancmIng", "1"): [remote_disconnected(), requests.exceptions.ReadTimeout("read timed out"), empty_list("ancmIng")],
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })
        crawler.session.post = fake

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert len(result) == 8
        assert fake.keys == [("ancmIng", "1")] * 3 + [("ancmPre", "1")]
        assert sleeps == [10, 60]
        msgs = warnings_of(caplog)
        assert any("1/3" in m and "ConnectionError" in m for m in msgs)
        assert any("2/3" in m and "ReadTimeout" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_retries_http_5xx(self, crawler, sleeps, caplog):
        fake = FakePost({
            ("ancmIng", "1"): [make_response("<html>Bad Gateway</html>", 502, "text/html"), empty_list("ancmIng")],
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })
        crawler.session.post = fake

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert len(result) == 8
        assert sleeps == [10]
        assert any("HTTP 502" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_retry_exhausted_returns_empty_with_warning(self, crawler, sleeps, caplog):
        fake = FakePost({("ancmIng", "1"): remote_disconnected()})
        crawler.session.post = fake

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        # 3회 시도 후 중단 — 사이트 장애로 보고 접수예정 목록은 요청하지 않음
        assert fake.keys == [("ancmIng", "1")] * 3
        assert sleeps == [10, 60]
        msgs = warnings_of(caplog)
        assert any("3/3" in m for m in msgs)
        assert any("0건" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_partial_result_when_later_request_fails(self, crawler, caplog):
        ing = load_json("iris_list_ancmEnd_p1.json")
        ing["paginationInfo"]["totalPageCount"] = 1
        crawler.session.post = FakePost({
            ("ancmIng", "1"): ing,
            ("ancmPre", "1"): make_response("unavailable", 503, "text/plain"),
        })

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert [a.id for a in result] == ["024258", "023897", "023817"]
        assert any("수집 중단" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_http_4xx_not_retried_and_status_logged(self, crawler, sleeps, caplog):
        """403은 재시도하지 않고 상태 코드를 남긴다 (Response.__bool__이 False여도 e.response is not None으로 판별).
        접수중 목록만 실패한 것이므로 접수예정은 계속 수집한다."""
        fake = FakePost({
            ("ancmIng", "1"): make_response("forbidden", 403, "text/html"),
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })
        crawler.session.post = fake

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert fake.keys == [("ancmIng", "1"), ("ancmPre", "1")]
        assert len(result) == 8
        assert all(a.category == "사업공고(접수예정)" for a in result)
        assert sleeps == []
        assert any("HTTP 403" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_non_json_response(self, crawler, caplog):
        crawler.session.post = FakePost({
            ("ancmIng", "1"): make_response("<html>점검 중</html>", 200, "text/html"),
            ("ancmPre", "1"): empty_list("ancmPre"),
        })

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert any("JSON 파싱 실패" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_missing_list_key(self, crawler, caplog):
        crawler.session.post = FakePost({
            ("ancmIng", "1"): {"result": "FAIL"},
            ("ancmPre", "1"): empty_list("ancmPre"),
        })

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert any("listBsnsAncmBtinSitu" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_unexpected_error_in_one_status_keeps_other(self, crawler, caplog):
        """접수중 처리 중 예상치 못한 예외가 나도 crawl()이 실패하지 않고 접수예정은 수집"""
        fake = FakePost({
            ("ancmIng", "1"): RuntimeError("boom"),
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })
        crawler.session.post = fake

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert fake.keys == [("ancmIng", "1"), ("ancmPre", "1")]
        assert len(result) == 8
        assert any("접수중" in m and "RuntimeError: boom" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_later_status_failure_keeps_earlier_status(self, crawler, caplog):
        ing = load_json("iris_list_ancmEnd_p1.json")
        ing["paginationInfo"]["totalPageCount"] = 1
        crawler.session.post = FakePost({
            ("ancmIng", "1"): ing,
            ("ancmPre", "1"): {"result": "FAIL"},
        })

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert [a.id for a in result] == ["024258", "023897", "023817"]
        assert any("접수예정" in m and "listBsnsAncmBtinSitu" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_zero_items_warning(self, crawler, caplog):
        crawler.session.post = FakePost({
            ("ancmIng", "1"): empty_list("ancmIng"),
            ("ancmPre", "1"): empty_list("ancmPre"),
        })

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert any("0건" in m for m in warnings_of(caplog))

    def test_default_retry_config(self):
        c = IitpCrawler()
        assert c.retry_delays == (10, 60)


class FakeGet:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TestGetDetail:
    @pytest.mark.asyncio
    async def test_content_and_attachments(self, crawler):
        fake = FakeGet(make_response(read_text("iris_view_024439.html"), 200, "text/html; charset=UTF-8"))
        crawler.session.get = fake

        detail = await crawler.get_detail("024439")

        assert fake.calls[0]["url"] == VIEW_URL
        assert fake.calls[0]["params"]["ancmId"] == "024439"
        content = detail["content"]
        assert "소관부처 과학기술정보통신부" in content
        assert "접수기간 2026-11-03 ~ 2026-11-16" in content
        assert "사업목적" in content
        # <span>으로 쪼개진 문단이 단어 중간에서 끊기지 않음
        assert "과학기술정보통신부 공고 제2026-1015호" in content
        assert "신규지원 대상과제를 다음과 같이 재공고하오니" in content
        # 담당자 연락처는 제외
        assert "연락처" not in content
        assert len(content) <= 5000

        atts = detail["attachments"]
        assert [a["name"].rsplit(".", 1)[-1] for a in atts] == ["hwp", "hwpx", "zip"]
        assert atts[0]["name"] == "1.  2026년도 AI기반주파수간섭분석및전파예측기술개발 신규과제 공고문.hwp"
        assert atts[0]["url"] == (
            f"{DOWNLOAD_URL}?atchDocId=il5zwUCl%2BCDPzDy%2FHJ8CbQ%3D%3D&atchFileId=BLUNm4i0zoJZRRsOHRDyrQ%3D%3D"
        )

    @pytest.mark.asyncio
    async def test_announcement_file_sorted_first(self, crawler):
        """공고문이 목록 뒤쪽에 있어도 맨 앞으로 (Bedrock은 첫 번째 지원 파일만 분석)"""
        html = read_text("iris_view_024439.html")
        start = html.index("<li>", html.index('class="add_file"'))
        first_end = html.index("</li>", start) + len("</li>")
        second_end = html.index("</li>", first_end) + len("</li>")
        swapped = html[:start] + html[first_end:second_end] + html[start:first_end] + html[second_end:]
        assert swapped.index("RFP") < swapped.index("공고문.hwp")
        crawler.session.get = FakeGet(make_response(swapped, 200, "text/html"))

        detail = await crawler.get_detail("024439")

        assert detail["attachments"][0]["name"].endswith("공고문.hwp")
        assert detail["attachments"][1]["name"].endswith(".hwpx")

    @pytest.mark.asyncio
    async def test_uses_ancm_prg_from_crawl(self, crawler):
        crawler.session.post = FakePost({
            ("ancmIng", "1"): empty_list("ancmIng"),
            ("ancmPre", "1"): load_json("iris_list_ancmPre.json"),
        })
        await crawler.crawl()
        fake = FakeGet(make_response(read_text("iris_view_024439.html"), 200, "text/html"))
        crawler.session.get = fake

        await crawler.get_detail("024439")

        assert fake.calls[0]["params"] == {"ancmId": "024439", "ancmPrg": "ancmPre"}

    @pytest.mark.asyncio
    async def test_http_error(self, crawler, caplog):
        crawler.session.get = FakeGet(make_response("not found", 404, "text/html"))

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await crawler.get_detail("999999") is None

        assert any("HTTP 404" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_connection_error(self, crawler, caplog):
        crawler.session.get = FakeGet(remote_disconnected())

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await crawler.get_detail("024439") is None

        assert any("ConnectionError" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_page_without_announcement(self, crawler, caplog):
        crawler.session.get = FakeGet(make_response("<html><body><p>오류</p></body></html>", 200, "text/html"))

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await crawler.get_detail("000000") is None

        assert any("구조 확인" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_download_attachment_uses_session(self, crawler):
        resp = make_response(b"\xd0\xcf\x11\xe0hwp", 200, "application/octet-stream")
        fake = FakeGet(resp)
        crawler.session.get = fake

        data = await crawler.download_attachment(f"{DOWNLOAD_URL}?atchDocId=a&atchFileId=b")

        assert data == b"\xd0\xcf\x11\xe0hwp"
        assert fake.calls[0]["url"].startswith(DOWNLOAD_URL)

    @pytest.mark.asyncio
    async def test_download_attachment_failure(self, crawler, caplog):
        crawler.session.get = FakeGet(make_response("err", 500, "text/html"))

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await crawler.download_attachment(f"{DOWNLOAD_URL}?atchDocId=a&atchFileId=b") is None

        assert any("HTTP 500" in m for m in warnings_of(caplog))
