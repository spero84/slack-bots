#!/usr/bin/env python3
"""일일 보고 1️⃣ Notion Kanban 섹션을 코드로 만든다.

예전에는 Claude CLI가 Notion MCP로 직접 조회하고 보고서 문구를 썼다. 그러다 보니 존재하지
않는 속성으로 조회해 태스크가 빠지거나, 57KB짜리 조회 결과를 파일로 넘겨받아 읽는 등 실행마다
결과가 달랐다. 이제는 Notion API로 조회한 뒤 Shawn이 Assign/Reviewer인 태스크만 코드에서 골라
섹션을 완성하고, Claude는 이 섹션을 그대로 보고서에 넣는다.

수동 실행 (섹션 미리보기):
    python -m src.scheduler.kanban
"""

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"  # data_sources API
HTTP_TIMEOUT = 30
KST = timezone(timedelta(hours=9))

SHAWN_NOTION_USER_ID = "20cd872b-594c-810b-9d99-0002e207a7c1"
# 보고서에 표시하는 순서. Holding(보류)은 Notion에서 'In progress' 그룹이지만 보고하지 않는다 —
# Kanban 섹션에도, 4️⃣ 액션 가이드에도 넣지 않는다 (사용자 요구, 2026-11-12). Backlogs/Done/Archive도 제외
EXCLUDED_STATUSES = ("Holding", "Backlogs", "Done", "Archive")
ACTIVE_STATUSES = ("In Progress", "In Review", "Ready (Commit)")
ROLE_LABELS = {"Assign": "담당", "Reviewer": "리뷰"}
NO_PART = "미분류"


@dataclass(frozen=True)
class Board:
    """name: 보고서 표기, people_properties: Shawn을 찾을 속성, due_properties: 앞에서부터 값이
    있는 날짜를 마감으로 쓴다, part_property: Part 구분으로 표시할 select 속성."""
    name: str
    data_source_id: str
    people_properties: tuple[str, ...]
    due_properties: tuple[str, ...]
    part_property: str


BOARDS: tuple[Board, ...] = (
    # LT Operation에는 Part 속성이 없어 같은 역할의 Work Type을 쓴다. Reviewer 속성도 없다.
    Board("LT Operation", "24431fc3-cdaa-81da-8b9b-000be37b9905",
          ("Assign",), ("Start & Due Date",), "Work Type"),
    Board("Management Support", "33631fc3-cdaa-8157-b790-000b885efad5",
          ("Assign", "Reviewer"), ("Deadline",), "Part"),
    # Searchdoc Operation은 대부분 Due Date를 쓰지만 일부 태스크는 Deadline에만 날짜가 있다
    Board("Searchdoc Operation", "abc31fc3-cdaa-8318-9439-87d69ae43b48",
          ("Assign", "Reviewer"), ("Due Date", "Deadline"), "Part"),
)


@dataclass(frozen=True)
class Task:
    title: str
    board: str
    status: str
    part: str
    roles: tuple[str, ...]  # ("담당",), ("리뷰",), ("담당", "리뷰")
    due: Optional[date]


def build_filter(board: Board) -> dict:
    """Shawn이 담당/리뷰어 속성 중 하나에 있고 활성 상태인 태스크만 조회하는 Notion 필터."""
    return {
        "and": [
            {"or": [{"property": prop, "people": {"contains": SHAWN_NOTION_USER_ID}}
                    for prop in board.people_properties]},
            {"or": [{"property": "Status", "status": {"equals": status}}
                    for status in ACTIVE_STATUSES]},
        ]
    }


class NotionClient:
    def __init__(self, token: Optional[str] = None, session: Optional[requests.Session] = None):
        self.token = token or os.environ.get("NOTION_TOKEN")
        if not self.token:
            raise RuntimeError("NOTION_TOKEN이 설정되지 않았다 (.env)")
        self.session = session or requests.Session()

    def query_data_source(self, data_source_id: str, filter_: dict) -> list[dict]:
        pages: list[dict] = []
        body: dict = {"filter": filter_, "page_size": 100}
        while True:
            resp = self.session.post(
                f"{NOTION_API}/data_sources/{data_source_id}/query",
                headers={"Authorization": f"Bearer {self.token}",
                         "Notion-Version": NOTION_VERSION,
                         "Content-Type": "application/json"},
                json=body,
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code >= 400:
                # Notion은 에러 원인을 본문 message에 담는다 (예: 없는 속성 이름)
                try:
                    message = resp.json().get("message", resp.text)
                except ValueError:
                    message = resp.text
                raise requests.HTTPError(f"{resp.status_code} {message}", response=resp)
            data = resp.json()
            pages.extend(data.get("results", []))
            if not data.get("has_more"):
                return pages
            body["start_cursor"] = data["next_cursor"]


def _plain_title(props: dict) -> str:
    for value in props.values():
        if value.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in value.get("title", [])).strip()
    return ""


def _parse_due(props: dict, due_properties: tuple[str, ...]) -> Optional[date]:
    """기간(start~end)이면 end를 마감으로 본다."""
    for prop in due_properties:
        value = (props.get(prop) or {}).get("date")
        if value:
            raw = value.get("end") or value.get("start")
            if raw:
                return date.fromisoformat(raw[:10])
    return None


def parse_task(page: dict, board: Board) -> Optional[Task]:
    """Shawn이 담당/리뷰어가 아니거나 활성 상태가 아니면 None (Notion 필터와 별개로 코드에서 재확인)."""
    props = page.get("properties", {})
    roles = tuple(
        ROLE_LABELS[prop] for prop in board.people_properties
        if any(p.get("id") == SHAWN_NOTION_USER_ID
               for p in (props.get(prop) or {}).get("people", []))
    )
    if not roles:
        return None
    status = ((props.get("Status") or {}).get("status") or {}).get("name")
    if status not in ACTIVE_STATUSES:
        return None
    part = ((props.get(board.part_property) or {}).get("select") or {}).get("name") or NO_PART
    return Task(title=_plain_title(props), board=board.name, status=status, part=part,
                roles=roles, due=_parse_due(props, board.due_properties))


def fetch_tasks(client: NotionClient) -> tuple[list[Task], list[str]]:
    """3개 보드를 조회한다. 보드 하나가 실패해도 나머지는 계속하고 실패한 보드 이름을 돌려준다."""
    tasks: list[Task] = []
    errors: list[str] = []
    for board in BOARDS:
        try:
            pages = client.query_data_source(board.data_source_id, build_filter(board))
        except requests.RequestException as e:
            logger.error(f"Notion 보드 조회 실패 ({board.name}): {e}")
            errors.append(f"{board.name}: {e}")
            continue
        board_tasks = [t for t in (parse_task(p, board) for p in pages) if t]
        logger.info(f"Notion {board.name}: {len(pages)}건 조회, {len(board_tasks)}건 보고 대상")
        tasks.extend(board_tasks)
    return tasks, errors


def _due_text(task: Task, today: date) -> str:
    if task.due is None:
        return "마감: 없음"
    if task.due == today:
        return "마감: *오늘* 🚨"
    if task.due < today:
        return f"마감: {task.due.isoformat()} ⚠️ *{(today - task.due).days}일 초과*"
    return f"마감: {task.due.isoformat()}"


def format_task_line(task: Task, today: date) -> str:
    return (f"> • `{task.part}` *{task.title}* — 보드: {task.board} · "
            f"역할: {'·'.join(task.roles)} · {_due_text(task, today)}")


def _sort_key(task: Task):
    # 마감 빠른 순, 마감 없는 태스크는 뒤로
    return (task.due is None, task.due or date.max, task.board, task.title)


def format_kanban_section(tasks: list[Task], errors: list[str], today: date) -> str:
    """보고서 1️⃣ 섹션 전체 (Slack mrkdwn)."""
    by_board = {b.name: sum(1 for t in tasks if t.board == b.name) for b in BOARDS}
    lines = [
        f"*1️⃣ Notion Kanban 태스크 현황* (총 {len(tasks)}건 — "
        + " · ".join(f"{name} {n}" for name, n in by_board.items()) + ")",
        "_Shawn이 담당(Assign) 또는 리뷰(Reviewer)인 태스크만 표시_",
    ]
    for status in ACTIVE_STATUSES:
        group = sorted((t for t in tasks if t.status == status), key=_sort_key)
        if not group:
            continue
        lines += ["", f"*`{status}` ({len(group)}건)*"]
        lines += [format_task_line(t, today) for t in group]

    due_today = sorted((t for t in tasks if t.due == today), key=_sort_key)
    overdue = sorted((t for t in tasks if t.due and t.due < today), key=lambda t: t.due)
    lines += ["", "🔴 *긴급/기한 초과*"]
    if due_today:
        lines += [f"> • 🚨 `{t.part}` *{t.title}* — 마감: *오늘* · 보드: {t.board}" for t in due_today]
    if overdue:
        oldest = ", ".join(f"{t.title}({(today - t.due).days}일)" for t in overdue[:3])
        lines.append(f"> • ⚠️ 기한 초과 {len(overdue)}건 — 오래된 순: {oldest}")
    if not due_today and not overdue:
        lines.append("> • 오늘 마감·기한 초과 태스크 없음")

    for error in errors:
        board_name, _, detail = error.partition(": ")
        lines.append(f"> ⚠️ *{board_name}* — 보드 조회 실패 ({detail})")
    return "\n".join(lines)


def build_kanban_section(client: Optional[NotionClient] = None,
                         today: Optional[date] = None) -> str:
    client = client or NotionClient()
    today = today or datetime.now(KST).date()
    tasks, errors = fetch_tasks(client)
    return format_kanban_section(tasks, errors, today)


def kanban_failure_section(error: str) -> str:
    """Notion 조회 자체가 불가능할 때 (토큰 없음 등) 보고서에 넣을 섹션."""
    return f"*1️⃣ Notion Kanban 태스크 현황*\n> ⚠️ Notion 조회 실패 — {error}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    print(build_kanban_section())
