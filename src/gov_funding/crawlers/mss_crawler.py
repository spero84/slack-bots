"""중소벤처기업부(MSS) 크롤러 - requests 기반

NIA와 동일한 BBS 시스템 (doBbsFView 패턴).
서버사이드 렌더링이므로 requests + BeautifulSoup으로 처리.
목록: List.do?cbIdx=310&pageIndex=N (페이지당 10건). ID는 doBbsFView('310','bcIdx',...)의 bcIdx만 쓴다
(제목 해시로 대체하면 마크업 변경 시 저장 키가 mss_<hex> ↔ mss_<bcIdx>로 바뀌어 같은 공고가 다시 알림된다).
"""
import asyncio
import copy
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from ..utils.dates import calculate_d_day, today_kst
from ..utils.text import clean_text
from .base_crawler import BaseCrawler

# 상세 페이지 메타 정보 (th → td)
DETAIL_META_LABELS = ("공고번호", "신청기간", "담당부서", "등록일")
_DATE = r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}"
_PERIOD_PATTERN = re.compile(rf"(?P<start>{_DATE})?\s*(?P<tilde>~)?\s*(?P<end>{_DATE})?")
# 첨부파일명 끝의 크기 표기: '[168.11 KB]'
_FILE_SIZE_SUFFIX = re.compile(r"\s*\[\s*[\d.,]+\s*[KMGT]?B\s*\]\s*$", re.IGNORECASE)
# 폴백 본문에서 버릴 내비게이션/버튼 줄
_NAV_LINES = {"이전글", "다음글", "전체목록", "바로보기", "내려받기", "첨부파일"}


class MssCrawler(BaseCrawler):
    """중소벤처기업부 크롤러 (서버사이드 렌더링)"""

    source = Source.MSS
    name = "MSS"

    def __init__(self, page_delay: Optional[float] = None, max_pages: Optional[int] = None):
        """
        Args:
            page_delay: 목록 페이지 요청 간 대기(초). None이면 CRAWL_SOURCES["mss"]["page_delay"]
            max_pages: 목록 최대 페이지 수. None이면 CRAWL_SOURCES["mss"]["max_pages"]
        """
        self.config = CRAWL_SOURCES["mss"]
        self.page_delay = page_delay if page_delay is not None else self.config.get("page_delay", 1.0)
        self.max_pages = max_pages if max_pages is not None else self.config.get("max_pages", 10)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 목록 크롤링

        최대 max_pages 페이지까지만 요청하고 페이지 사이에 page_delay만큼 대기한다.
        한 페이지에서 새 공고 ID가 하나도 나오지 않으면(pageIndex가 무시돼 같은 목록이 반복되는 경우) 중단한다.
        요청/파싱이 실패하면 그때까지 수집한 공고를 반환한다.
        """
        announcements: list[Announcement] = []
        seen_ids: set[str] = set()

        for page in range(1, self.max_pages + 1):
            if page > 1:
                # page_delay=0이어도 이벤트 루프에 제어를 넘긴다
                await asyncio.sleep(self.page_delay)

            try:
                items = self._crawl_page(page)
            except requests.RequestException as e:
                status = e.response.status_code if e.response is not None else None
                logger.warning(
                    f"MSS 페이지 {page} 요청 실패 (HTTP {status}) - 수집 중단 (지금까지 {len(announcements)}건 유지): {e}"
                )
                break
            except Exception as e:
                logger.warning(f"MSS 페이지 {page} 처리 오류 - 수집 중단 (지금까지 {len(announcements)}건 유지): {e}")
                break

            if not items:
                break

            added = 0
            for ann in items:
                if ann.id not in seen_ids:
                    seen_ids.add(ann.id)
                    announcements.append(ann)
                    added += 1

            if not added:
                logger.warning(
                    f"MSS 페이지 {page}: {len(items)}건이 모두 이전 페이지와 중복 "
                    f"- pageIndex 파라미터가 무시되는지 확인 필요, 수집 중단"
                )
                break

            logger.info(f"MSS 페이지 {page}: {added}건 추가 (총 {len(announcements)}건)")
            if len(announcements) >= max_items:
                break
        else:
            logger.info(f"MSS 최대 페이지 수({self.max_pages}) 도달 - 수집 종료")

        result = announcements[:max_items]
        if not result:
            logger.warning("MSS 크롤링 결과 0건 - 목록 페이지 구조 변경 여부 확인 필요")
        else:
            logger.info(f"MSS 크롤링 완료: 총 {len(result)}건")
        return result

    def _crawl_page(self, page: int) -> list[Announcement]:
        """단일 목록 페이지 요청 + 파싱"""
        params = {"pageIndex": page}

        response = self.session.get(self.config["list_url"], params=params, timeout=30)
        response.raise_for_status()
        return self.parse_list_page(response.text)

    def parse_list_page(self, html: str) -> list[Announcement]:
        """목록 HTML 파싱

        현재 MSS 구조는 table > tbody > tr(onclick=doBbsFView)이므로 테이블을 먼저 본다.
        li 구조(NIA BBS 패턴)는 테이블에서 한 건도 못 찾았을 때만 폴백으로 쓴다 —
        li를 먼저 보면 사이드 메뉴 등 엉뚱한 'ul li'에 doBbsFView 링크가 하나만 있어도 테이블 파싱이 생략된다.
        """
        soup = BeautifulSoup(html, "lxml")
        announcements = []

        for tr in soup.select("table tbody tr"):
            try:
                ann = self._parse_item_tr(tr)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"MSS tr 파싱 오류: {e}")

        if not announcements:
            for li in soup.select("ul li"):
                try:
                    ann = self._parse_item_li(li)
                    if ann:
                        announcements.append(ann)
                except Exception as e:
                    logger.debug(f"MSS li 파싱 오류: {e}")

        return announcements

    def _parse_item_li(self, li) -> Optional[Announcement]:
        """<li> 아이템 파싱 (NIA BBS 패턴)

        구조:
        <li>
          <a href="#view" onclick="doBbsFView('310','bcIdx','Gbn','parentSeq')" title="제목">
            제목
            <span class="date">2026.02.09</span>
            <span>부서명</span>
          </a>
        </li>
        """
        link = li.find("a")
        if not link:
            return None

        onclick = link.get("onclick", "")
        bcIdx = None
        parentSeq = "0"

        # doBbsFView('310','bcIdx','Gbn','parentSeq') 패턴
        view_match = re.search(
            r"doBbsFView\s*\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,\s*'?([^']*)'?\s*,\s*'?(\d+)'?\s*\)",
            onclick
        )
        if view_match:
            bcIdx = view_match.group(2)
            parentSeq = view_match.group(4)
        else:
            return None

        # 제목 추출
        title_attr = link.get("title", "")
        raw_title = re.sub(r'-첨부파일\s*(있음|없음)$', '', title_attr).strip()
        raw_title = re.sub(r'\(새\s*글\)$', '', raw_title).strip()

        if not raw_title:
            raw_title = link.get_text(strip=True)
            # 내부 span 텍스트 제거
            for span in link.find_all("span"):
                span_text = span.get_text(strip=True)
                raw_title = raw_title.replace(span_text, "").strip()

        if not raw_title:
            return None

        # [카테고리] prefix 분리
        category = None
        cat_match = re.match(r'\[([^\]]+)\]\s*', raw_title)
        if cat_match:
            category = cat_match.group(1)
            title = raw_title[cat_match.end():].strip()
        else:
            title = raw_title

        if not title:
            return None

        # 게시일 추출
        posted_date = None
        date_span = link.find("span", class_="date")
        if date_span:
            date_text = date_span.get_text(strip=True)
            for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
                try:
                    posted_date = datetime.strptime(date_text, fmt)
                    break
                except ValueError:
                    continue

        # 신청기간에서 마감일 추출
        deadline = None
        d_day = None
        full_text = link.get_text()
        period_match = re.search(
            r'(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})\s*~\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})',
            full_text,
        )
        if period_match:
            end_date_str = period_match.group(2).replace('.', '-').replace('/', '-')
            try:
                deadline = datetime.strptime(end_date_str, "%Y-%m-%d")
                deadline = deadline.replace(hour=23, minute=59, second=59)
                d_day = calculate_d_day(deadline, today_kst())
            except ValueError:
                pass

        # 부서명 추출
        organization = None
        for span in link.find_all("span"):
            text = span.get_text(strip=True)
            if re.search(r'(팀|부|실|센터|단|본부|처|국|과|TF)$', text):
                organization = text

        # 상세 URL 생성
        url = self.config["detail_url_template"].format(
            bcIdx=bcIdx,
            parentSeq=parentSeq,
        )

        return Announcement(
            id=bcIdx,
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=d_day,
            department="중소벤처기업부",
            organization=organization or "MSS",
            url=url,
            posted_date=posted_date,
        )

    def _parse_item_tr(self, tr) -> Optional[Announcement]:
        """<tr> 아이템 파싱 (테이블 구조)

        MSS BBS 구조:
        <tr onclick="doBbsFView('310','bcIdx','Gbn','parentSeq')">
          <td>번호</td>
          <td class="subject"><a href="#view">제목</a></td>
          ...
        </tr>
        """
        cells = tr.find_all("td")
        if len(cells) < 3:
            return None

        # 제목 셀 찾기 (a 태그 포함 셀)
        title_cell = None
        for cell in cells:
            if cell.find("a"):
                title_cell = cell
                break

        if not title_cell:
            return None

        link = title_cell.find("a")
        title = link.get_text(strip=True)
        if not title:
            return None

        # ID 추출: <tr> 태그의 onclick에서 doBbsFView 추출
        ann_id = None
        parentSeq = "0"

        # <tr> onclick 우선 (현재 MSS 구조)
        tr_onclick = tr.get("onclick", "")
        view_match = re.search(
            r"doBbsFView\s*\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,\s*'?([^']*)'?\s*,\s*'?(\d+)'?\s*\)",
            tr_onclick
        )
        if view_match:
            ann_id = view_match.group(2)
            parentSeq = view_match.group(4)

        # <a> onclick 폴백
        if not ann_id:
            link_onclick = link.get("onclick", "")
            view_match = re.search(
                r"doBbsFView\s*\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,\s*'?([^']*)'?\s*,\s*'?(\d+)'?\s*\)",
                link_onclick
            )
            if view_match:
                ann_id = view_match.group(2)
                parentSeq = view_match.group(4)

        # href 폴백
        if not ann_id:
            href = link.get("href", "")
            bcIdx_match = re.search(r'bcIdx=(\d+)', href)
            if bcIdx_match:
                ann_id = bcIdx_match.group(1)

        if not ann_id:
            # 제목 해시로 대체하지 않는다 — 2026-02에 mss_<hex> → mss_<bcIdx>로 키가 바뀌어 같은 공고가 재알림됐다
            logger.warning(f"MSS 게시물 번호(bcIdx)를 찾을 수 없어 건너뜀: {title}")
            return None

        # 카테고리 (공고번호 등)
        category = None
        cat_match = re.match(r'\[([^\]]+)\]\s*', title)
        if cat_match:
            category = cat_match.group(1)
            title = title[cat_match.end():].strip()

        # 신청기간에서 마감일 추출
        deadline = None
        d_day = None
        full_text = tr.get_text()
        period_match = re.search(
            r'(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})\s*~\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})',
            full_text,
        )
        if period_match:
            end_date_str = period_match.group(2).replace('.', '-').replace('/', '-')
            try:
                deadline = datetime.strptime(end_date_str, "%Y-%m-%d")
                deadline = deadline.replace(hour=23, minute=59, second=59)
                d_day = calculate_d_day(deadline, today_kst())
            except ValueError:
                pass

        # 부서명 추출
        organization = None
        for span in tr.find_all("span"):
            text = span.get_text(strip=True)
            if re.search(r'(팀|부|실|센터|단|본부|처|국|과|TF)$', text):
                organization = text

        # 날짜 추출
        posted_date = None
        for cell in reversed(cells):
            date_text = cell.get_text(strip=True)
            for fmt in ("%Y.%m.%d", "%Y-%m-%d"):
                try:
                    posted_date = datetime.strptime(date_text, fmt)
                    break
                except ValueError:
                    continue
            if posted_date:
                break

        url = self.config["detail_url_template"].format(
            bcIdx=ann_id,
            parentSeq=parentSeq,
        )

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=d_day,
            department="중소벤처기업부",
            organization=organization or "MSS",
            url=url,
            posted_date=posted_date,
        )

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회

        Returns:
            {"content": 메타 정보 + 본문 (일반 텍스트), "attachments": [{"name", "url"}, ...]}
            또는 None (요청 실패)
        """
        url = self.config["detail_url_template"].format(
            bcIdx=announcement_id,
            parentSeq="0",
        )

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return self.parse_detail_page(response.text)
        except Exception as e:
            logger.error(f"MSS 상세 조회 오류 ({announcement_id}): {e}")
            return None

    def parse_detail_page(self, html_text: str) -> dict:
        """상세 페이지 HTML 파싱

        본문은 숨김 textarea#editContents 안에 엔티티 이스케이프된 HTML로 들어 있다
        (화면에는 iframe이 클라이언트에서 렌더링). div.board_view 전체에 get_text를 하면
        '<br />', '&nbsp;' 같은 태그/엔티티와 PC·모바일 메타 중복, 첨부 라벨, 이전글/다음글 제목이
        섞여 모델이 그대로 요약에 옮겼다. 그래서 메타(th→td)와 본문(textarea)을 따로 뽑는다.
        """
        soup = BeautifulSoup(html_text, "lxml")
        view = soup.select_one("div.board_view") or soup

        meta_line = self._parse_detail_meta(view)
        attachments = self._parse_attachments(soup)
        body = self._parse_detail_body(soup)
        content = "\n".join(part for part in (meta_line, body) if part)

        return {
            "content": content[:5000],
            "attachments": attachments,
        }

    @staticmethod
    def _format_period(value: str) -> Optional[str]:
        """신청기간 메타 → 'label: value' (빈 값/'~'만 있으면 None)

        종료일이 없는 기간('2026-07-27 ~')이나 날짜가 아닌 값('상시')에는 '신청기간' 키워드를 쓰지 않는다.
        쓰면 마감일 추출기(parse_deadline_from_text)가 뒤따르는 등록일을 마감일로 오인한다.
        """
        m = _PERIOD_PATTERN.fullmatch(value.strip())
        if not m:
            return f"신청 일정: {value}" if value.strip(" ~") else None
        start, tilde, end = m.group("start"), m.group("tilde"), m.group("end")
        if start and end:
            return f"신청기간: {start} ~ {end}"
        if end:
            return f"신청기간: ~ {end}"
        if start and tilde:
            return f"신청 시작일: {start} (종료일 미기재)"
        if start:
            return f"신청기간: {start}"
        return None

    def _parse_detail_meta(self, view) -> str:
        """th → td 쌍에서 공고번호/신청기간/담당부서/등록일을 한 줄로"""
        meta: dict[str, str] = {}
        for th in view.select("th"):
            label = th.get_text(strip=True)
            if label not in DETAIL_META_LABELS or label in meta:
                continue  # PC/모바일 행에 같은 항목이 중복된다
            td = th.find_next_sibling("td")
            if td is None:
                continue
            meta[label] = re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()

        parts = []
        for label in DETAIL_META_LABELS:
            value = meta.get(label, "")
            if label == "신청기간":
                formatted = self._format_period(value)
                if formatted:
                    parts.append(formatted)
            elif value:
                parts.append(f"{label}: {value}")
        return " | ".join(parts)

    def _parse_detail_body(self, soup) -> str:
        """본문 텍스트 (textarea#editContents → 일반 텍스트, 없으면 기존 선택자 폴백)"""
        textarea = soup.select_one("textarea#editContents")
        if textarea is not None:
            inner_html = textarea.get_text()
            if not inner_html.strip():
                return ""
            text = BeautifulSoup(inner_html, "lxml").get_text("\n", strip=True)
            return clean_text(text)

        # 폴백: 구 레이아웃 — 메타/첨부 행, 이전글/다음글, 버튼 영역을 제거하고 텍스트 추출
        container = soup.select_one(
            "div.board_view, div.view_cont, div.content, "
            "div.bbs_view, div.bbsV_cont"
        )
        if container is None:
            return ""
        container = copy.copy(container)  # 원본 soup(첨부파일 목록 등)은 건드리지 않는다
        for el in container.select(
            "caption, h4#sj, ul.prev_next_view, div.btn_box, td.file_list, script, style"
        ):
            el.decompose()
        for th in container.select("th"):
            td = th.find_next_sibling("td")
            if td is not None:
                td.decompose()
            th.decompose()
        lines = [
            line for line in container.get_text("\n", strip=True).split("\n")
            if line.strip() not in _NAV_LINES
        ]
        return clean_text("\n".join(lines))

    def _parse_attachments(self, soup) -> list[dict]:
        """첨부파일 목록 (크기 표기 제거, 절대 URL, '공고'가 들어간 파일 우선)"""
        base_url = self.config["base_url"].rstrip("/") + "/"
        attachments: list[dict] = []
        seen: set[str] = set()

        def add(name: str, href: str) -> None:
            name = _FILE_SIZE_SUFFIX.sub("", name).strip()
            href = (href or "").strip()
            if not name or not href or href.lower().startswith("javascript"):
                return
            url = urljoin(base_url, href)
            if url in seen:
                return
            seen.add(url)
            attachments.append({"name": name, "url": url})

        # td.file_list 구조: span.name(파일명 + <em>[크기]</em>) + a.type_down(Download.do)
        for li in soup.select("td.file_list li"):
            name_span = li.select_one("span.name")
            down_link = li.select_one(
                "a[href*='Download.do'], a[href*='Download'], a[href*='download']"
            )
            if name_span and down_link:
                add(name_span.get_text(" ", strip=True), down_link.get("href", ""))

        # td.file_list가 없는 경우 폴백
        if not attachments:
            for a in soup.select(
                "a[href*='Download'], a[href*='download'], "
                "a[href*='fileDown'], a.file_down"
            ):
                add(a.get_text(" ", strip=True), a.get("href", ""))

        # 공고문 우선 (안정 정렬 — 나머지는 게시 순서 유지)
        attachments.sort(key=lambda att: 0 if "공고" in att["name"] else 1)
        return attachments

    async def close(self):
        """세션 종료"""
        self.session.close()
