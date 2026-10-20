"""JointipsCrawler 테스트 (공개 이벤트 API 실제 응답을 축약한 fixture 사용, 네트워크 없음)"""
import json
import logging
from datetime import date, datetime
from pathlib import Path

import pytest
import requests

from src.gov_funding.crawlers import jointips_crawler
from src.gov_funding.crawlers.jointips_crawler import JointipsCrawler, parse_datetime
from src.gov_funding.storage import Source

FIXTURES = Path(__file__).parent / "fixtures" / "jointips"
LIST_API = "https://jointips.or.kr/api/cms/public/event/list"
DETAIL_API = "https://jointips.or.kr/api/cms/public/event/{id}"
ATTACH_API = "https://jointips.or.kr/api/cms/store/list-by-path"
LOGGER = "gov-funding-monitor"


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_response(body, status: int = 200, content_type: str = "application/json") -> requests.Response:
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


class FakeGet:
    """URL별 응답을 돌려주는 session.get 대체"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        if url not in self.routes:
            raise AssertionError(f"예상치 못한 요청: {url}")
        item = self.routes[url]
        if callable(item) and not isinstance(item, requests.Response):
            item = item(params or {})
        if isinstance(item, Exception):
            raise item
        if isinstance(item, requests.Response):
            return item
        return make_response(item)


@pytest.fixture
def crawler(monkeypatch):
    # fixture 응답 수집일(2026-10-20 KST) 기준
    monkeypatch.setattr(jointips_crawler, "today_kst", lambda: date(2026, 10, 20))
    c = JointipsCrawler()
    yield c


def warnings_of(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and r.name == LOGGER]


class TestParseDatetime:
    @pytest.mark.parametrize("text,expected", [
        ("2026-10-13 16:00", datetime(2026, 10, 13, 16, 0)),
        ("2026-10-13 16:00:30", datetime(2026, 10, 13, 16, 0, 30)),
        ("2026-10-13", datetime(2026, 10, 13, 23, 59, 59)),
    ])
    def test_formats(self, text, expected):
        assert parse_datetime(text) == expected

    @pytest.mark.parametrize("text", [None, "", "상시", "2026-13-01"])
    def test_invalid(self, text):
        assert parse_datetime(text) is None


class TestCrawl:
    @pytest.mark.asyncio
    async def test_mapping(self, crawler):
        crawler.session.get = FakeGet({LIST_API: load_json("event_list_latest.json")})

        result = await crawler.crawl(max_items=50)

        assert len(result) == 6
        first = result[0]
        assert first.id == "Wo4Kkb"
        assert first.source == Source.JOINTIPS
        assert first.title == "2026 경기 스타트업 서밋(G-SUMMIT 2026)"
        assert first.category == "포럼"
        assert first.organization == "경기도/경기도경제과학진흥원"
        assert first.department == "TIPS"
        assert first.deadline == datetime(2026, 10, 13, 16, 0)
        assert first.posted_date == datetime(2026, 10, 7, 14, 24)
        assert first.url == "https://jointips.or.kr/contents/events/detail?eventId=Wo4Kkb"
        assert first.summary.startswith("<G-SUMMIT 2026>")
        assert len(first.summary) <= 200

    @pytest.mark.asyncio
    async def test_d_day_uses_kst_today(self, crawler):
        crawler.session.get = FakeGet({LIST_API: load_json("event_list_latest.json")})

        result = {a.id: a for a in await crawler.crawl()}

        # 2026-10-21 18:00 마감, 오늘 2026-10-20 → D-1
        assert result["rqVKoW"].d_day == 1
        # 이미 마감 → None
        assert result["Wo4Kkb"].d_day is None

    @pytest.mark.asyncio
    async def test_keeps_closed_events_and_api_order(self, crawler):
        """availableOnly 필터 없이 전부 수집, sort=latest(등록일 내림차순) 순서 유지"""
        fake = FakeGet({LIST_API: load_json("event_list_latest.json")})
        crawler.session.get = fake

        result = await crawler.crawl()

        assert [a.id for a in result] == ["Wo4Kkb", "26wJXr", "rqVKoW", "WG8OYb", "WDQxBb", "bj7Ko2"]
        posted = [a.posted_date for a in result]
        assert posted == sorted(posted, reverse=True)
        params = fake.calls[0]["params"]
        assert params == {"siteId": 4, "page": 1, "size": 50, "sort": "latest"}
        assert "availableOnly" not in params

    @pytest.mark.asyncio
    async def test_page_size_follows_max_items(self, crawler):
        fake = FakeGet({LIST_API: load_json("event_list_latest.json")})
        crawler.session.get = fake

        result = await crawler.crawl(max_items=3)

        assert len(result) == 3
        assert fake.calls[0]["params"]["size"] == 3
        assert len(fake.calls) == 1

    @pytest.mark.asyncio
    async def test_dedupes_and_skips_rows_without_id(self, crawler):
        data = load_json("event_list_latest.json")
        data["data"].append(dict(data["data"][0]))
        data["data"].append({**data["data"][1], "encodedId": None})
        crawler.session.get = FakeGet({LIST_API: data})

        result = await crawler.crawl()

        assert len(result) == 6

    @pytest.mark.asyncio
    async def test_malformed_row_is_skipped(self, crawler, caplog):
        """형식이 이상한 항목 하나(organizer가 배열)는 WARNING 후 건너뛰고 나머지는 수집 (crawl()이 실패하지 않음)"""
        data = load_json("event_list_latest.json")
        data["data"][2]["organizer"] = ["A", "B"]
        data["data"][4]["title"] = 123
        crawler.session.get = FakeGet({LIST_API: data})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert [a.id for a in result] == ["Wo4Kkb", "26wJXr", "WG8OYb", "bj7Ko2"]
        msgs = warnings_of(caplog)
        assert any("항목 파싱 오류" in m and "encodedId=rqVKoW" in m for m in msgs)
        assert any("항목 파싱 오류" in m and "encodedId=WDQxBb" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_missing_dates(self, crawler):
        data = load_json("event_list_latest.json")
        row = data["data"][0]
        for key in ("applyEndDt", "applyEndDate", "regDt"):
            row.pop(key)
        crawler.session.get = FakeGet({LIST_API: data})

        first = (await crawler.crawl())[0]

        assert first.deadline is None
        assert first.d_day is None
        assert first.posted_date is None

    @pytest.mark.asyncio
    async def test_api_error_code(self, crawler, caplog):
        crawler.session.get = FakeGet({LIST_API: {"code": 500, "message": "Internal Server Error"}})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        msgs = warnings_of(caplog)
        assert any("code=500" in m for m in msgs)
        assert any("0건" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_http_404_logs_warning_with_status(self, crawler, caplog):
        """404를 DEBUG가 아닌 WARNING으로, 상태 코드 포함 (e.response is not None 규칙)"""
        crawler.session.get = FakeGet({LIST_API: make_response("<title>요청하신 페이지를 찾을 수 없습니다</title>", 404, "text/html")})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert any("HTTP 404" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_connection_error(self, crawler, caplog):
        crawler.session.get = FakeGet({LIST_API: requests.exceptions.ConnectionError("boom")})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert any("ConnectionError" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_non_json(self, crawler, caplog):
        crawler.session.get = FakeGet({LIST_API: make_response("<html></html>", 200, "text/html")})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert any("JSON 파싱 실패" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_empty_list_warning(self, crawler, caplog):
        crawler.session.get = FakeGet({LIST_API: {"code": 200, "total": 0, "page": 1, "size": 50, "data": []}})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            result = await crawler.crawl()

        assert result == []
        assert any("0건" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_paginates_when_max_items_exceeds_page_size(self, crawler):
        page1 = load_json("event_list_latest.json")
        page1["size"] = 3
        page1["data"] = page1["data"][:3]
        page2 = load_json("event_list_latest.json")
        page2["data"] = page2["data"][3:5]  # 페이지 크기보다 적음 → 마지막 페이지
        crawler.config = {**crawler.config, "page_size": 3}
        fake = FakeGet({LIST_API: lambda params: page1 if params["page"] == 1 else page2})
        crawler.session.get = fake

        result = await crawler.crawl(max_items=10)

        assert [c["params"]["page"] for c in fake.calls] == [1, 2]
        assert [a.id for a in result] == ["Wo4Kkb", "26wJXr", "rqVKoW", "WG8OYb", "WDQxBb"]

    @pytest.mark.asyncio
    async def test_stops_when_page_repeats(self, crawler):
        """page 파라미터가 무시되어 같은 목록이 반복되면 더 요청하지 않음"""
        page = load_json("event_list_latest.json")
        page["data"] = page["data"][:3]
        crawler.config = {**crawler.config, "page_size": 3}
        fake = FakeGet({LIST_API: page})
        crawler.session.get = fake

        result = await crawler.crawl(max_items=10)

        assert [c["params"]["page"] for c in fake.calls] == [1, 2]
        assert len(result) == 3


class TestGetDetail:
    @pytest.mark.asyncio
    async def test_content(self, crawler):
        url = DETAIL_API.format(id="rqVKoW")
        fake = FakeGet({url: load_json("event_detail_rqVKoW.json")})
        crawler.session.get = fake

        detail = await crawler.get_detail("rqVKoW")

        assert fake.calls[0]["params"] == {"siteId": 4}
        content = detail["content"]
        assert "신한퓨처스랩" in content
        assert "신청기간: 2026-09-30 09:00 ~ 2026-10-21 18:00" in content
        assert "신청 링크: https://app.quotabook.com/" in content
        assert "<div" not in content
        assert "안녕하세요. 신한퓨처스랩입니다." in content
        assert len(content) <= 5000
        # attachGroupId 없음 → 첨부 조회 안 함
        assert detail["attachments"] == []
        assert len(fake.calls) == 1

    @pytest.mark.asyncio
    async def test_attachments_when_group_present(self, crawler):
        """attachGroupId가 있으면 첨부 목록 API 조회 — 공고문 우선, 실제 확장자 유지"""
        event = load_json("event_detail_rqVKoW.json")
        event["data"]["attachGroupId"] = "grp-1"
        files = {"code": 200, "data": [
            {"id": "f-2", "realName": "참가신청서.docx", "fileName": "a1b2.docx"},
            {"id": "f-1", "realName": "2026 모집 공고문.pdf", "fileName": "c3d4.pdf"},
            {"id": None, "realName": "id없음.hwp"},
        ]}
        fake = FakeGet({DETAIL_API.format(id="rqVKoW"): event, ATTACH_API: files})
        crawler.session.get = fake

        detail = await crawler.get_detail("rqVKoW")

        assert detail["attachments"] == [
            {"name": "2026 모집 공고문.pdf", "url": "https://jointips.or.kr/api/cms/store/download/f-1"},
            {"name": "참가신청서.docx", "url": "https://jointips.or.kr/api/cms/store/download/f-2"},
        ]
        assert fake.calls[1]["params"] == {"path": "event", "groupId": "grp-1"}

    @pytest.mark.asyncio
    async def test_attachments_bare_list_response(self, crawler):
        event = load_json("event_detail_rqVKoW.json")
        event["data"]["attachGroupId"] = "grp-1"
        crawler.session.get = FakeGet({
            DETAIL_API.format(id="rqVKoW"): event,
            ATTACH_API: [{"id": "f-9", "fileName": "안내.hwp"}],
        })

        detail = await crawler.get_detail("rqVKoW")

        assert detail["attachments"] == [{"name": "안내.hwp", "url": "https://jointips.or.kr/api/cms/store/download/f-9"}]

    @pytest.mark.asyncio
    async def test_attachment_api_failure_keeps_content(self, crawler, caplog):
        event = load_json("event_detail_rqVKoW.json")
        event["data"]["attachGroupId"] = "grp-1"
        crawler.session.get = FakeGet({
            DETAIL_API.format(id="rqVKoW"): event,
            ATTACH_API: make_response("err", 500, "text/plain"),
        })

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            detail = await crawler.get_detail("rqVKoW")

        assert "신한퓨처스랩" in detail["content"]
        assert detail["attachments"] == []
        assert any("HTTP 500" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_http_error(self, crawler, caplog):
        crawler.session.get = FakeGet({DETAIL_API.format(id="nope"): make_response("not found", 404, "text/html")})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await crawler.get_detail("nope") is None

        assert any("HTTP 404" in m for m in warnings_of(caplog))

    @pytest.mark.asyncio
    async def test_error_code(self, crawler, caplog):
        crawler.session.get = FakeGet({DETAIL_API.format(id="gone"): {"code": 404, "message": "not found"}})

        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert await crawler.get_detail("gone") is None

        assert any("code=404" in m for m in warnings_of(caplog))
