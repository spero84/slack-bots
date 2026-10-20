"""JOINTIPS(조인팁스) 크롤러 - requests 기반 (공개 JSON API)

2026-09 사이트가 신규 CMS로 이전되면서 gnuboard URL(bbs/board.php?bo_table=event&wr_id=N)은 301 → 404.
신규 사이트의 이벤트 목록/상세는 공개 JSON API로 렌더링된다 (인증 불필요).

목록: GET https://jointips.or.kr/api/cms/public/event/list?siteId=4&page=1&size=50&sort=latest
- sort=latest는 등록일(regDt) 내림차순
- 응답 {code: 200, total, page, size, data: [{encodedId, title, eventStdNm, organizer, applyEndDt('YYYY-MM-DD HH:MM'),
  regDt, applyStatus(OPEN/CLOSED), summary, contents(HTML), applicationUrl, ...}]}
- 공개 ID는 encodedId뿐 (eventId는 null)
상세: GET https://jointips.or.kr/api/cms/public/event/{encodedId}?siteId=4 → {code: 200, data: {...}}
첨부: data.attachGroupId가 있으면 GET /api/cms/store/list-by-path?path=event&groupId=... →
  /api/cms/store/download/{id} (상세 페이지 JS 기준)
사람용 URL: https://jointips.or.kr/contents/events/detail?eventId={encodedId}
"""
import re
from datetime import date, datetime
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from ..utils.dates import calculate_d_day, today_kst
from .base_crawler import BaseCrawler

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def parse_datetime(text: Optional[str]) -> Optional[datetime]:
    """'YYYY-MM-DD HH:MM' 또는 'YYYY-MM-DD'(→ 23:59:59) 파싱"""
    if not text:
        return None
    text = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        return None


_BLOCK_TAGS = ["p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"]


def _element_text(el) -> str:
    """요소 텍스트 추출 (블록 요소 단위 줄바꿈, 스크립트 제외)

    에디터 본문은 한 문단이 여러 <span>으로 쪼개져 있어 get_text("\\n")을 쓰면 단어 중간에서 줄이 깨진다.
    """
    for tag in el.select("script, style"):
        tag.decompose()
    for br in el.find_all("br"):
        br.replace_with("\n")
    for tag in el.find_all(["td", "th"]):
        tag.append(" ")
    for tag in el.find_all(_BLOCK_TAGS):
        tag.append("\n")
    lines = (re.sub(r"[ \t\u00a0\u3000]+", " ", line).strip() for line in el.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def html_to_text(html: Optional[str]) -> str:
    """본문 HTML → 텍스트"""
    if not html:
        return ""
    return _element_text(BeautifulSoup(html, "lxml"))


def _attachment_priority(name: str) -> int:
    """첨부파일 정렬 우선순위 — 공고문이 먼저"""
    if "공고문" in name:
        return 0
    if "공고" in name or "모집" in name:
        return 1
    return 2


class JointipsApiError(Exception):
    """JOINTIPS API 호출 실패"""


class JointipsCrawler(BaseCrawler):
    """JOINTIPS 크롤러 (공개 이벤트 JSON API)"""

    source = Source.JOINTIPS
    name = "JOINTIPS"

    def __init__(self, timeout: float = 30):
        self.config = CRAWL_SOURCES["jointips"]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    def _get_json(
        self, url: str, params: Optional[dict] = None, what: str = "API", require_code: bool = True
    ) -> Any:
        """GET + JSON 파싱 (require_code=True면 code != 200을 실패로 처리)

        Raises:
            JointipsApiError: HTTP 오류, 네트워크 오류, JSON 파싱 실패, code != 200
        """
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            raise JointipsApiError(f"{what} HTTP {status}") from e
        except requests.exceptions.RequestException as e:
            raise JointipsApiError(f"{what} {type(e).__name__}: {e}") from e

        try:
            data = response.json()
        except ValueError as e:
            raise JointipsApiError(
                f"{what} JSON 파싱 실패 ({type(e).__name__}, content-type={response.headers.get('content-type')})"
            ) from e

        if require_code and (not isinstance(data, dict) or data.get("code") != 200):
            code = data.get("code") if isinstance(data, dict) else type(data).__name__
            message = data.get("message") if isinstance(data, dict) else None
            raise JointipsApiError(f"{what} 비정상 응답 (code={code}, message={message})")
        return data

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """최신 등록순 이벤트 목록 수집

        applyStatus와 무관하게 전부 수집한다 (마감/키워드/Bedrock 필터가 관련성 판단).
        """
        today = today_kst()
        page_size = max(1, min(max_items, self.config.get("page_size", 50)))
        max_pages = self.config.get("max_pages", 3)
        announcements: list[Announcement] = []
        seen_ids: set[str] = set()
        page = 1

        while len(announcements) < max_items and page <= max_pages:
            params = {
                "siteId": self.config["site_id"],
                "page": page,
                "size": page_size,
                "sort": "latest",
            }
            try:
                data = self._get_json(self.config["list_api_url"], params=params, what="목록")
            except JointipsApiError as e:
                logger.warning(f"JOINTIPS 목록 조회 실패 (page={page}): {e}")
                break

            rows = data.get("data")
            if not isinstance(rows, list):
                logger.warning(f"JOINTIPS 목록 응답에 data 배열 없음 (page={page}, 키={list(data)[:10]})")
                break

            added = 0
            for row in rows:
                # 형식이 이상한 항목 하나 때문에 crawl() 전체가 실패하지 않도록 건너뛴다
                try:
                    ann = self._parse_event(row, today)
                except Exception as e:
                    row_id = row.get("encodedId") if isinstance(row, dict) else None
                    logger.warning(
                        f"JOINTIPS 항목 파싱 오류 - 건너뜀 (page={page}, encodedId={row_id}): {type(e).__name__}: {e}"
                    )
                    continue
                if ann is None or ann.id in seen_ids:
                    continue
                seen_ids.add(ann.id)
                announcements.append(ann)
                added += 1

            total = data.get("total")
            logger.info(f"JOINTIPS 페이지 {page}: {len(rows)}건 (전체 {total}건, 누적 {len(announcements)}건)")
            # 마지막 페이지이거나, page 파라미터가 무시되어 같은 목록이 반복되면 중단
            if not added or len(rows) < page_size or (isinstance(total, int) and page * page_size >= total):
                break
            page += 1

        result = announcements[:max_items]
        if not result:
            logger.warning("JOINTIPS 크롤링 결과 0건 - 이벤트 API/사이트 구조 변경 여부 확인 필요")
        else:
            logger.info(f"JOINTIPS 크롤링 완료: 총 {len(result)}건")
        return result

    def _parse_event(self, row: dict, today: Optional[date] = None) -> Optional[Announcement]:
        """이벤트 JSON → Announcement"""
        if not isinstance(row, dict):
            return None
        event_id = str(row.get("encodedId") or "").strip()
        title = (row.get("title") or "").strip()
        if not event_id or not title:
            logger.debug(f"JOINTIPS ID/제목 없는 항목 스킵: {str(row)[:200]}")
            return None

        deadline = parse_datetime(row.get("applyEndDt")) or parse_datetime(row.get("applyEndDate"))
        summary = (row.get("summary") or "").strip()

        return Announcement(
            id=event_id,
            source=self.source,
            title=title,
            category=(row.get("eventStdNm") or "").strip() or None,
            deadline=deadline,
            d_day=calculate_d_day(deadline, today or today_kst()),
            department="TIPS",
            organization=(row.get("organizer") or "").strip() or None,
            url=self.config["detail_url_template"].format(id=event_id),
            posted_date=parse_datetime(row.get("regDt")),
            summary=summary[:200] or None,
        )

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """이벤트 상세 (본문 텍스트 + 첨부파일)

        Returns:
            {'content': str, 'attachments': [{'name': str, 'url': str}]}. 실패 시 None
        """
        url = self.config["detail_api_url"].format(id=announcement_id)
        try:
            data = self._get_json(url, params={"siteId": self.config["site_id"]}, what="상세")
        except JointipsApiError as e:
            logger.warning(f"JOINTIPS 상세 조회 실패 ({announcement_id}): {e}")
            return None

        event = data.get("data")
        if not isinstance(event, dict):
            logger.warning(f"JOINTIPS 상세 응답에 data 없음 ({announcement_id})")
            return None

        return {
            "content": self._build_content(event)[:5000],
            "attachments": self._fetch_attachments(event.get("attachGroupId")),
        }

    @staticmethod
    def _build_content(event: dict) -> str:
        """상세 메타 정보 + 본문 텍스트"""
        meta = [
            ("구분", event.get("eventStdNm")),
            ("주최/주관", event.get("organizer")),
            ("장소", event.get("location")),
            ("신청기간", " ~ ".join(filter(None, [event.get("applyStartDt"), event.get("applyEndDt")]))),
            ("행사기간", " ~ ".join(filter(None, [event.get("eventStartDt"), event.get("eventEndDt")]))),
            ("모집대상/정원", event.get("capacityText")),
            ("신청 링크", event.get("applicationUrl")),
        ]
        lines = [f"{label}: {str(value).strip()}" for label, value in meta if value and str(value).strip()]
        body = html_to_text(event.get("contents")) or (event.get("summary") or "").strip()
        if body:
            lines.append(body)
        return "\n".join(lines)

    def _fetch_attachments(self, group_id: Optional[str]) -> list[dict]:
        """첨부파일 목록 조회 (attachGroupId가 있을 때만)"""
        if not group_id:
            return []
        try:
            data = self._get_json(
                self.config["attach_api_url"],
                params={"path": "event", "groupId": group_id},
                what="첨부 목록",
                require_code=False,
            )
        except JointipsApiError as e:
            logger.warning(f"JOINTIPS 첨부 목록 조회 실패 (groupId={group_id}): {e}")
            return []

        # 상세 페이지 JS: (json.data || json) — 배열 그대로 오는 경우도 처리
        files = data.get("data") if isinstance(data, dict) else data
        if not isinstance(files, list):
            return []

        attachments = []
        for f in files:
            if not isinstance(f, dict) or not f.get("id"):
                continue
            name = (f.get("realName") or f.get("fileName") or "").strip()
            if not name:
                continue
            attachments.append({
                "name": name,
                "url": self.config["file_download_url_template"].format(id=f["id"]),
            })
        attachments.sort(key=lambda a: _attachment_priority(a["name"]))
        return attachments

    async def close(self):
        """세션 종료"""
        self.session.close()
