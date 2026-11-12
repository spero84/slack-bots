"""scheduler 1️⃣ Notion Kanban 섹션 생성 테스트"""

from datetime import date

import pytest
import requests

from src.scheduler.kanban import (
    ACTIVE_STATUSES,
    BOARDS,
    NO_PART,
    SHAWN_NOTION_USER_ID,
    NotionClient,
    Task,
    EXCLUDED_STATUSES,
    build_filter,
    build_kanban_section,
    fetch_tasks,
    format_kanban_section,
    format_task_line,
    kanban_failure_section,
    parse_task,
)

TODAY = date(2026, 11, 5)
SHAWN = {"object": "user", "id": SHAWN_NOTION_USER_ID, "name": "Kim Shawn"}
OTHER = {"object": "user", "id": "35ed872b-594c-8168-9def-0002f052afcb", "name": "kim jiyeon"}
GROUP = {"object": "group", "id": "3d031fc3-cdaa-81a9-9580-0088e18316ec", "name": "Rocket Infra Team"}

LT, MGMT, SEARCHDOC = BOARDS


def page(title, status="In Progress", assign=(), reviewer=None, part=None, part_prop="Part",
         dates=None):
    props = {
        "Name": {"type": "title", "title": [{"plain_text": title}]},
        "Status": {"type": "status", "status": {"name": status}},
        "Assign": {"type": "people", "people": list(assign)},
        part_prop: {"type": "select", "select": {"name": part} if part else None},
    }
    if reviewer is not None:
        props["Reviewer"] = {"type": "people", "people": list(reviewer)}
    for prop, value in (dates or {}).items():
        props[prop] = {"type": "date", "date": value}
    return {"object": "page", "properties": props}


class TestBoards:
    def test_3개_보드_고정(self):
        assert [b.name for b in BOARDS] == ["LT Operation", "Management Support", "Searchdoc Operation"]
        assert [b.data_source_id for b in BOARDS] == [
            "24431fc3-cdaa-81da-8b9b-000be37b9905",
            "33631fc3-cdaa-8157-b790-000b885efad5",
            "abc31fc3-cdaa-8318-9439-87d69ae43b48",
        ]

    def test_보드별_담당_리뷰어_속성(self):
        """LT Operation에는 Reviewer 속성이 없고, Searchdoc Operation에는 Person 속성이 없다."""
        assert LT.people_properties == ("Assign",)
        assert MGMT.people_properties == ("Assign", "Reviewer")
        assert SEARCHDOC.people_properties == ("Assign", "Reviewer")

    def test_보드별_Part_속성(self):
        assert LT.part_property == "Work Type"  # LT Operation에는 Part 속성이 없다
        assert MGMT.part_property == "Part"
        assert SEARCHDOC.part_property == "Part"

    def test_필터는_Shawn_담당_리뷰어와_활성_상태(self):
        f = build_filter(SEARCHDOC)
        assert f["and"][0] == {"or": [
            {"property": "Assign", "people": {"contains": SHAWN_NOTION_USER_ID}},
            {"property": "Reviewer", "people": {"contains": SHAWN_NOTION_USER_ID}},
        ]}
        assert [c["status"]["equals"] for c in f["and"][1]["or"]] == list(ACTIVE_STATUSES)

    def test_상태값_Ready_Commit_형식(self):
        assert set(ACTIVE_STATUSES) == {"Ready (Commit)", "In Progress", "In Review"}

    @pytest.mark.parametrize("board", [LT, MGMT, SEARCHDOC], ids=lambda b: b.name)
    def test_Holding은_조회_조건에_없음(self, board):
        """Holding(보류)은 Kanban 섹션과 액션 가이드 모두에서 뺀다 (2026-11-12 요구)."""
        assert set(EXCLUDED_STATUSES).isdisjoint(ACTIVE_STATUSES)
        statuses = [c["status"]["equals"] for c in build_filter(board)["and"][1]["or"]]
        assert "Holding" not in statuses


class TestParseTask:
    def test_담당(self):
        t = parse_task(page("A", assign=[SHAWN, OTHER], reviewer=[], part="Product"), SEARCHDOC)
        assert t.roles == ("담당",)
        assert t.part == "Product"

    def test_리뷰(self):
        t = parse_task(page("A", assign=[OTHER], reviewer=[SHAWN], part="인사 업무"), MGMT)
        assert t.roles == ("리뷰",)

    def test_담당_리뷰(self):
        t = parse_task(page("A", assign=[SHAWN], reviewer=[SHAWN]), SEARCHDOC)
        assert t.roles == ("담당", "리뷰")

    def test_Shawn이_아니면_제외(self):
        """Notion 필터를 통과했더라도 코드에서 다시 확인해 제외한다."""
        assert parse_task(page("A", assign=[OTHER], reviewer=[OTHER]), MGMT) is None

    def test_그룹만_지정된_태스크는_제외(self):
        assert parse_task(page("A", assign=[GROUP], reviewer=[OTHER]), SEARCHDOC) is None

    def test_LT는_Reviewer_속성을_보지_않음(self):
        """LT Operation 보드는 Assign만 기준이다."""
        assert parse_task(page("A", assign=[OTHER], reviewer=[SHAWN]), LT) is None

    @pytest.mark.parametrize("status", ["Backlogs", "Holding", "Done", "Archive"])
    def test_비활성_상태는_제외(self, status):
        assert parse_task(page("A", status=status, assign=[SHAWN]), LT) is None

    def test_Part_없으면_미분류(self):
        t = parse_task(page("A", assign=[SHAWN], part=None, part_prop="Work Type"), LT)
        assert t.part == NO_PART

    def test_LT는_Work_Type을_Part로(self):
        t = parse_task(page("A", assign=[SHAWN], part="HR", part_prop="Work Type"), LT)
        assert t.part == "HR"

    def test_마감_기간이면_end(self):
        t = parse_task(page("A", assign=[SHAWN], reviewer=[],
                            dates={"Due Date": {"start": "2026-08-07", "end": "2026-08-12"}}), SEARCHDOC)
        assert t.due == date(2026, 8, 12)

    def test_Due_Date_비면_Deadline(self):
        t = parse_task(page("A", assign=[SHAWN], reviewer=[],
                            dates={"Due Date": None, "Deadline": {"start": "2026-12-31", "end": None}}),
                       SEARCHDOC)
        assert t.due == date(2026, 12, 31)

    def test_마감_없음(self):
        assert parse_task(page("A", assign=[SHAWN]), LT).due is None


def task(title="A", status="In Progress", part="Product", roles=("담당",), due=None,
         board="Searchdoc Operation"):
    return Task(title=title, board=board, status=status, part=part, roles=roles, due=due)


class TestFormat:
    def test_태스크_줄_맨_앞에_Part(self):
        line = format_task_line(task(title="Sales deck 개발", part="영업·마케팅·파트너",
                                     roles=("담당", "리뷰"), due=date(2026, 9, 23)), TODAY)
        assert line == ("> • `영업·마케팅·파트너` *Sales deck 개발* — 보드: Searchdoc Operation · "
                        "역할: 담당·리뷰 · 마감: 2026-09-23 ⚠️ *43일 초과*")

    def test_오늘_마감(self):
        assert "마감: *오늘* 🚨" in format_task_line(task(due=TODAY), TODAY)

    def test_마감_없음_표시(self):
        assert format_task_line(task(due=None), TODAY).endswith("마감: 없음")

    def test_상태별_그룹과_순서(self):
        section = format_kanban_section([
            task("R", status="Ready (Commit)"), task("V", status="In Review"), task("P"),
        ], [], TODAY)
        assert section.index("*`In Progress` (1건)*") < section.index("*`In Review` (1건)*") \
            < section.index("*`Ready (Commit)` (1건)*")

    def test_마감_빠른_순_마감_없는_태스크는_뒤(self):
        section = format_kanban_section([
            task("없음", due=None), task("늦음", due=date(2026, 12, 1)), task("빠름", due=date(2026, 9, 1)),
        ], [], TODAY)
        assert section.index("*빠름*") < section.index("*늦음*") < section.index("*없음*")

    def test_헤더_보드별_건수(self):
        section = format_kanban_section([task(board="LT Operation"), task(), task()], [], TODAY)
        assert section.startswith("*1️⃣ Notion Kanban 태스크 현황* (총 3건 — "
                                  "LT Operation 1 · Management Support 0 · Searchdoc Operation 2)")

    def test_긴급_기한_초과_요약(self):
        section = format_kanban_section([
            task("오늘건", due=TODAY), task("옛날건", due=date(2026, 9, 18)), task("최근건", due=date(2026, 11, 1)),
        ], [], TODAY)
        assert "> • 🚨 `Product` *오늘건* — 마감: *오늘* · 보드: Searchdoc Operation" in section
        assert "> • ⚠️ 기한 초과 2건 — 오래된 순: 옛날건(48일), 최근건(4일)" in section

    def test_긴급_없음(self):
        assert "오늘 마감·기한 초과 태스크 없음" in format_kanban_section([task()], [], TODAY)

    def test_보드_조회_실패_표시(self):
        section = format_kanban_section([], ["Management Support: 400 validation_error"], TODAY)
        assert "> ⚠️ *Management Support* — 보드 조회 실패 (400 validation_error)" in section

    def test_실패_섹션(self):
        assert "Notion 조회 실패 — NOTION_TOKEN" in kanban_failure_section("NOTION_TOKEN 없음")


class FakeClient:
    def __init__(self, pages_by_board, fail=()):
        self.pages_by_board = pages_by_board
        self.fail = fail
        self.filters = {}

    def query_data_source(self, data_source_id, filter_):
        self.filters[data_source_id] = filter_
        if data_source_id in self.fail:
            raise requests.HTTPError("400 Could not find property with name or id: Person")
        return self.pages_by_board.get(data_source_id, [])


class TestFetchTasks:
    def test_보드별_필터로_조회하고_Shawn_태스크만(self):
        client = FakeClient({
            LT.data_source_id: [page("lt", assign=[SHAWN], part_prop="Work Type")],
            MGMT.data_source_id: [page("mine", assign=[OTHER], reviewer=[SHAWN]),
                                  page("not mine", assign=[OTHER], reviewer=[OTHER])],
        })
        tasks, errors = fetch_tasks(client)
        assert [t.title for t in tasks] == ["lt", "mine"]
        assert errors == []
        assert client.filters[MGMT.data_source_id] == build_filter(MGMT)

    def test_보드_하나_실패해도_나머지_계속(self):
        client = FakeClient({LT.data_source_id: [page("lt", assign=[SHAWN], part_prop="Work Type")]},
                            fail=(SEARCHDOC.data_source_id,))
        tasks, errors = fetch_tasks(client)
        assert [t.title for t in tasks] == ["lt"]
        assert len(errors) == 1 and errors[0].startswith("Searchdoc Operation: 400")

    def test_build_kanban_section(self):
        client = FakeClient({MGMT.data_source_id: [page("등기임원", assign=[OTHER], reviewer=[SHAWN],
                                                        part="인사 업무",
                                                        dates={"Deadline": {"start": "2026-10-30"}})]})
        section = build_kanban_section(client, today=TODAY)
        assert "> • `인사 업무` *등기임원* — 보드: Management Support · 역할: 리뷰 · " \
               "마감: 2026-10-30 ⚠️ *6일 초과*" in section


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class TestNotionClient:
    def test_토큰_없으면_에러(self, monkeypatch):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="NOTION_TOKEN"):
            NotionClient()

    def test_페이지네이션과_헤더(self):
        session = FakeSession([
            FakeResponse({"results": [{"id": "1"}], "has_more": True, "next_cursor": "c2"}),
            FakeResponse({"results": [{"id": "2"}], "has_more": False}),
        ])
        client = NotionClient(token="secret", session=session)
        assert [p["id"] for p in client.query_data_source("ds", {"x": 1})] == ["1", "2"]
        url, kwargs = session.calls[0]
        assert url == "https://api.notion.com/v1/data_sources/ds/query"
        assert kwargs["headers"]["Authorization"] == "Bearer secret"
        assert kwargs["headers"]["Notion-Version"] == "2025-09-03"
        assert kwargs["json"]["filter"] == {"x": 1}
        assert session.calls[1][1]["json"]["start_cursor"] == "c2"

    def test_에러_메시지_포함(self):
        session = FakeSession([FakeResponse(
            {"message": "Could not find property with name or id: Person"}, status=400)])
        client = NotionClient(token="secret", session=session)
        with pytest.raises(requests.HTTPError, match="400 Could not find property"):
            client.query_data_source("ds", {})
