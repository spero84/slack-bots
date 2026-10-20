"""NIA(한국지능정보사회진흥원) 크롤러 - requests 기반

두 개 게시판 크롤링:
- 입찰공고 (cbIdx=78336): 사전규격공개, 조달입찰공고, 입찰공고(재공고), 평가결과 공개 ...
- 공지사항 (cbIdx=99835): 일반 공지 게시판 (별도 '사업공고' 게시판은 없음)

목록 행 구조 (2026-10 기준):
<li class="nia_noti">   ← 상단 고정 공지 (1페이지에만 노출)
  <a href="#view" onclick="doBbsFView('cbIdx','bcIdx','메뉴코드','parentSeq');return false;"
     title="[카테고리] 제목(새 글)-첨부파일 있음">
    <i><span class="subject">[카테고리] 제목 ...</span>
       <span class="src"><em>2026.10.15</em><em>조회수 943</em></span>
       <span class="writer"><em>권남희</em><em>재무관리팀</em></span></i>
  </a>
</li>

목록에는 마감일이 없으므로 게시일(posted_date)로 deadline_filter가 오래된 글(고정 공지 등)을 걸러낸다.
"""
import asyncio
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler

PAGE_SIZE = 10

VIEW_PATTERN = re.compile(
    r"doBbsFView\s*\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,\s*'?([^',]*)'?\s*,\s*'?(\d+)'?\s*\)"
)
DEPT_PATTERN = re.compile(r"(팀|부|실|센터|단|본부|처|국|과|TF)$")

# 공고문이 아닌 첨부파일 (Bedrock은 첫 번째 지원 형식 파일만 읽으므로 뒤로 보낸다)
NON_NOTICE_KEYWORDS = ("양식", "서식", "신청서", "동의서", "서약서", "확인서", "포스터")


def _clean(text: Optional[str]) -> str:
    """연속 공백 정리"""
    return re.sub(r"\s+", " ", text or "").strip()


def _text_lines(element) -> str:
    """줄 구분을 유지하며 텍스트 추출 (태그 경계에서 글자가 붙지 않도록)"""
    lines = (_clean(line) for line in element.get_text("\n", strip=True).splitlines())
    return "\n".join(line for line in lines if line)


def sort_attachments(attachments: list[dict]) -> list[dict]:
    """공고문을 맨 앞에, 양식·포스터류를 맨 뒤에 둔다 (같은 순위 안에서는 원래 순서 유지)

    '제안요청서(재공고)'처럼 '공고'만 들어간 파일보다 '입찰공고문'을 앞에 둔다.
    """
    def priority(att: dict) -> int:
        name = att["name"]
        if any(k in name for k in NON_NOTICE_KEYWORDS):
            return 3
        if "공고문" in name:
            return 0
        if "공고" in name:
            return 1
        return 2

    return sorted(attachments, key=priority)


class NiaCrawler(BaseCrawler):
    """NIA 크롤러 (서버사이드 렌더링, 두 게시판)"""

    source = Source.NIA
    name = "NIA"

    def __init__(self, page_delay: Optional[float] = None, max_pages: Optional[int] = None):
        self.config = CRAWL_SOURCES["nia"]
        self.page_delay = page_delay if page_delay is not None else self.config.get("page_delay", 0.7)
        self.max_pages = max_pages if max_pages is not None else self.config.get("max_pages", 5)
        self.skip_categories = set(self.config.get("skip_categories", []))
        self.skip_title_keywords = list(self.config.get("skip_title_keywords", []))
        # crawl() 중 확인한 bcIdx → (cbIdx, parentSeq). get_detail()에서 게시판 추측 요청을 줄인다
        self._board_by_id: dict[str, tuple[str, str]] = {}
        self._last_request_at: Optional[float] = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    async def _get(self, url: str, params: Optional[dict] = None) -> str:
        """요청 간격(page_delay)을 지키며 GET 후 HTML 반환"""
        if self.page_delay and self._last_request_at is not None:
            wait = self.page_delay - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
        try:
            response = self.session.get(url, params=params, timeout=30)
        finally:
            self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response.text

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """두 게시판에서 공고 크롤링"""
        all_announcements = []
        boards = self.config["boards"]
        per_board_max = max(1, max_items // len(boards))

        for board in boards:
            try:
                board_items = await self._crawl_board(board, per_board_max)
            except Exception as e:
                logger.warning(f"NIA {board['name']} 크롤링 오류: {e}")
                continue
            if board_items:
                logger.info(f"NIA {board['name']} 크롤링 완료: {len(board_items)}건")
            else:
                logger.warning(f"NIA {board['name']} 크롤링 결과 0건 - 사이트 구조 변경 여부 확인 필요")
            all_announcements.extend(board_items)

        result = all_announcements[:max_items]
        if not result:
            logger.warning("NIA 크롤링 결과 0건 - 사이트 구조 변경 여부 확인 필요")
        else:
            logger.info(f"NIA 크롤링 완료: 총 {len(result)}건")
        return result

    async def _crawl_board(self, board: dict, max_items: int) -> list[Announcement]:
        """단일 게시판 크롤링 (행이 10건 미만인 페이지 또는 max_pages에서 중단)"""
        announcements: list[Announcement] = []
        seen_ids: set[str] = set()

        for page in range(1, self.max_pages + 1):
            try:
                html = await self._get(board["list_url"], params={"pageIndex": page})
                items, row_count = self.parse_list_page(html, board)
            except requests.RequestException as e:
                status = e.response.status_code if e.response is not None else None
                logger.warning(f"NIA {board['name']} 페이지 {page} 요청 실패 (HTTP {status}): {e}")
                break
            except Exception as e:
                logger.warning(f"NIA {board['name']} 페이지 {page} 처리 오류: {e}")
                break

            if row_count == 0:
                break

            for ann in items:
                if ann.id not in seen_ids:
                    seen_ids.add(ann.id)
                    announcements.append(ann)

            logger.info(
                f"NIA {board['name']} 페이지 {page}: {row_count}건 중 {len(items)}건 수집 "
                f"(누적 {len(announcements)}건)"
            )

            if len(announcements) >= max_items or row_count < PAGE_SIZE:
                break

        return announcements[:max_items]

    def parse_list_page(self, html: str, board: dict) -> tuple[list[Announcement], int]:
        """목록 페이지 HTML 파싱

        Returns:
            (수집 대상 공고 목록, 게시글 행 수). 행 수는 제외 규칙 적용 전 값으로 페이지 끝 판단에 쓴다.
        """
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("div.board_type01 > ul > li") or soup.select("ul li")

        announcements = []
        row_count = 0
        for li in rows:
            link = li.find("a", onclick=VIEW_PATTERN)
            if link is None:
                continue  # 네비게이션 링크 등
            row_count += 1
            try:
                ann = self._parse_item(li, link, board)
            except Exception as e:
                logger.debug(f"NIA 아이템 파싱 오류: {e}")
                continue
            if ann is None:
                continue
            if self._should_skip(ann):
                logger.debug(f"NIA 제외 ({ann.category}): {ann.title}")
                continue
            announcements.append(ann)

        return announcements, row_count

    def _should_skip(self, ann: Announcement) -> bool:
        """지원 대상이 아닌 글 (평가결과 공개, 취소공고)"""
        if ann.category in self.skip_categories:
            return True
        text = f"{ann.category or ''} {ann.title}"
        return any(keyword in text for keyword in self.skip_title_keywords)

    def _parse_item(self, li, link, board: dict) -> Optional[Announcement]:
        """<li> 아이템 파싱"""
        view_match = VIEW_PATTERN.search(link.get("onclick", ""))
        if not view_match:
            return None
        bc_idx = view_match.group(2)
        parent_seq = view_match.group(4)

        # 제목: title 속성에서 "-첨부파일 있음", "(새 글)" 접미사 제거
        raw_title = re.sub(r"-첨부파일\s*(있음|없음)$", "", _clean(link.get("title"))).strip()
        raw_title = re.sub(r"\(새\s*글\)$", "", raw_title).strip()
        if not raw_title:
            subject = link.select_one("span.subject")
            # em(첨부/new 아이콘)을 제외한 직접 텍스트만
            raw_title = _clean(" ".join(subject.find_all(string=True, recursive=False))) if subject else ""
        if not raw_title:
            return None

        # [카테고리] prefix 분리
        cat_match = re.match(r"\[([^\]]+)\]\s*", raw_title)
        if cat_match:
            category = cat_match.group(1).strip()
            title = raw_title[cat_match.end():].strip()
        else:
            category = board["name"]
            title = raw_title
        if not title:
            return None

        self._board_by_id[bc_idx] = (board["cbIdx"], parent_seq)

        return Announcement(
            id=bc_idx,
            source=self.source,
            title=title,
            category=category,
            deadline=None,  # NIA 목록에 마감일 정보 없음
            d_day=None,
            department="한국지능정보사회진흥원",
            organization=self._parse_organization(link) or "NIA",
            url=self.config["detail_url_template"].format(
                cbIdx=board["cbIdx"], bcIdx=bc_idx, parentSeq=parent_seq,
            ),
            posted_date=self._parse_posted_date(link),
        )

    @staticmethod
    def _parse_posted_date(link) -> Optional[datetime]:
        """게시일: span.src의 첫 번째 em ('2026.10.15'), 구 마크업 span.date도 지원"""
        candidates = []
        first_em = link.select_one("span.src em")
        if first_em:
            candidates.append(first_em.get_text(strip=True))
        date_span = link.find("span", class_="date")
        if date_span:
            candidates.append(date_span.get_text(strip=True))

        for text in candidates:
            for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
        return None

    @staticmethod
    def _parse_organization(link) -> Optional[str]:
        """담당 부서: span.writer의 마지막 em (첫 em은 담당자 이름)

        span.writer 전체 텍스트는 '권남희재무관리팀'처럼 이름과 부서가 붙어 있으므로 쓰지 않는다.
        """
        writer_ems = [_clean(em.get_text(" ", strip=True)) for em in link.select("span.writer em")]
        writer_ems = [text for text in writer_ems if text]
        if len(writer_ems) >= 2:
            return writer_ems[-1]

        candidates = list(writer_ems)
        dept = link.find("span", class_="dept")
        if dept:
            candidates.append(_clean(dept.get_text(" ", strip=True)))
        organization = None
        for text in candidates:
            if DEPT_PATTERN.search(text):
                organization = text
        return organization

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회

        crawl()에서 확인한 게시판을 먼저 시도하고, 모르면 두 게시판을 차례로 시도한다.
        게시판이 틀리면 '보여 줄 항목이 없습니다.' alert 페이지가 온다.
        """
        candidates: list[tuple[str, str]] = []
        cached = self._board_by_id.get(announcement_id)
        if cached:
            candidates.append(cached)
        for board in self.config["boards"]:
            if cached is None or board["cbIdx"] != cached[0]:
                candidates.append((board["cbIdx"], announcement_id))

        for cb_idx, parent_seq in candidates:
            url = self.config["detail_url_template"].format(
                cbIdx=cb_idx, bcIdx=announcement_id, parentSeq=parent_seq,
            )
            try:
                html = await self._get(url)
            except requests.RequestException as e:
                status = e.response.status_code if e.response is not None else None
                logger.warning(f"NIA 상세 조회 실패 ({announcement_id}, cbIdx={cb_idx}, HTTP {status}): {e}")
                continue

            detail = self.parse_detail_page(html)
            if detail:
                return detail
            logger.debug(f"NIA 상세 없음 ({announcement_id}, cbIdx={cb_idx}) - 다음 게시판 시도")

        logger.warning(f"NIA 상세 조회 실패 ({announcement_id}) - 본문을 찾을 수 없음")
        return None

    def parse_detail_page(self, html: str) -> Optional[dict]:
        """상세 페이지 HTML 파싱

        div.detail_type01 > div.tit_area(제목), div.fileNew_area(첨부), div.con_area(본문)

        Returns:
            {'content', 'attachments'} 또는 None (게시판이 틀린 alert 페이지 등)
        """
        soup = BeautifulSoup(html, "lxml")
        root = soup.select_one("div.detail_type01")
        body = soup.select_one("div.con_area")
        if root is None and body is None:
            return None

        parts = []
        title_el = soup.select_one("div.detail_type01 div.tit_area")
        if title_el:
            parts.append(_clean(title_el.get_text(" ", strip=True)))

        if body is None:
            body = soup.select_one(
                "div.view_cont, div.board_view, div.bbs_view, div.bbsV_cont"
            ) or root
        body_text = _text_lines(body)
        if body_text:
            parts.append(body_text)

        attachments = self._parse_attachments(soup)
        content = "\n".join(parts)
        if not body_text and not attachments:
            return None

        return {
            "content": content[:5000],
            "attachments": attachments,
        }

    def _parse_attachments(self, soup: BeautifulSoup) -> list[dict]:
        """첨부파일: div.fileNew_area span.file_nm > a[href*='Download.do']

        같은 파일의 다운로드 버튼(절대 URL, 이름 'xxx.hwp-다운로드')과 미리보기 버튼이 함께 있으므로
        URL 기준으로 중복을 제거하고 미리보기는 건너뛴다.
        """
        links = soup.select("div.fileNew_area span.file_nm a[href]")
        if not links:
            links = soup.select(
                "a[href*='Download'], a[href*='download'], a[href*='fileDown'], a.file_down"
            )

        attachments = []
        seen_urls = set()
        for a in links:
            href = (a.get("href") or "").strip()
            if not href or href.lower().startswith("javascript"):
                continue
            name = _clean(a.get_text(" ", strip=True))
            if name.endswith("-미리보기"):
                continue
            if name.endswith("-다운로드"):
                name = name[: -len("-다운로드")].strip()
            if not name or name == "다운로드":
                continue
            url = urljoin(self.config["base_url"], href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            attachments.append({"name": name, "url": url})

        return sort_attachments(attachments)

    async def close(self):
        """세션 종료"""
        self.session.close()
