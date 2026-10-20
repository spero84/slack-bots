"""NIPA(정보통신산업진흥원) 크롤러 - requests 기반

목록: https://www.nipa.kr/home/2-2?curPage=N (서버사이드 렌더링, 페이지당 10건, 작성일 내림차순)
상세: https://www.nipa.kr/home/2-2/{id}
- div.tbWrap.gonggo.detail table.tb05의 '내용' 행이 본문, '첨부파일' 행에 a[href^='/comm/getFile']
- 첨부파일 링크 텍스트에 '(파일크기: 168 KB )' 접미사가 붙어 확장자 판별을 방해하므로 제거한다
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
from ..utils.dates import calculate_d_day, today_kst
from .base_crawler import BaseCrawler

FILE_SIZE_SUFFIX = re.compile(r"\s*\(\s*파일\s*크기\s*:[^)]*\)\s*$")

# 공고문이 아닌 첨부파일 (Bedrock은 첫 번째 지원 형식 파일만 읽으므로 뒤로 보낸다)
NON_NOTICE_KEYWORDS = ("양식", "서식", "신청서", "동의서", "서약서", "확인서", "포스터")


def _clean(text: Optional[str]) -> str:
    """연속 공백 정리"""
    return re.sub(r"\s+", " ", text or "").strip()


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


class NipaCrawler(BaseCrawler):
    """NIPA 크롤러 (서버사이드 렌더링)"""

    source = Source.NIPA
    name = "NIPA"

    def __init__(self, page_delay: Optional[float] = None, max_pages: Optional[int] = None):
        """
        Args:
            page_delay: 요청 간 대기(초). None이면 CRAWL_SOURCES["nipa"]["page_delay"]
            max_pages: 목록 최대 페이지 수. None이면 CRAWL_SOURCES["nipa"]["max_pages"]
        """
        self.config = CRAWL_SOURCES["nipa"]
        self.page_delay = page_delay if page_delay is not None else self.config.get("page_delay", 0.7)
        self.max_pages = max_pages if max_pages is not None else self.config.get("max_pages", 10)
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
        """공고 목록 크롤링

        최대 max_pages 페이지까지만 요청한다. 한 페이지에서 새 공고 ID가 하나도 나오지 않으면
        (curPage 파라미터가 무시돼 같은 목록이 반복되는 경우) 중단한다 — 상한이 없으면 무한 반복한다.
        요청이 실패하면 그때까지 수집한 공고를 반환한다.
        """
        announcements: list[Announcement] = []
        seen_ids: set[str] = set()

        for page in range(1, self.max_pages + 1):
            # main.py는 모든 크롤러를 하나의 try로 감싸므로 예외를 밖으로 던지지 않는다
            try:
                html = await self._get(self.config["list_url"], params={"curPage": page})
                items = self.parse_list_page(html)
            except requests.RequestException as e:
                status = e.response.status_code if e.response is not None else None
                logger.warning(
                    f"NIPA 페이지 {page} 요청 실패 (HTTP {status}) - 수집 중단 (지금까지 {len(announcements)}건 유지): {e}"
                )
                break
            except Exception as e:
                logger.warning(f"NIPA 페이지 {page} 처리 오류 - 수집 중단 (지금까지 {len(announcements)}건 유지): {e}")
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
                    f"NIPA 페이지 {page}: {len(items)}건이 모두 이전 페이지와 중복 "
                    f"- curPage 파라미터가 무시되는지 확인 필요, 수집 중단"
                )
                break

            logger.info(f"NIPA 페이지 {page} 크롤링 완료: {len(items)}건 중 {added}건 추가 (누적 {len(announcements)}건)")
            if len(announcements) >= max_items:
                break
        else:
            logger.info(f"NIPA 최대 페이지 수({self.max_pages}) 도달 - 수집 종료")

        result = announcements[:max_items]
        if not result:
            logger.warning("NIPA 크롤링 결과 0건 - 사이트 구조 변경 여부 확인 필요")
        else:
            logger.info(f"NIPA 크롤링 완료: 총 {len(result)}건")
        return result

    def parse_list_page(self, html: str) -> list[Announcement]:
        """목록 페이지 HTML 파싱"""
        soup = BeautifulSoup(html, "lxml")

        table = soup.select_one("table.tbgg") or soup.find("table")
        if not table:
            logger.warning("NIPA 테이블을 찾을 수 없음")
            return []

        tbody = table.find("tbody")
        if not tbody:
            logger.warning("NIPA tbody를 찾을 수 없음")
            return []

        announcements = []
        for row in tbody.find_all("tr"):
            try:
                ann = self._parse_row(row)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"NIPA 행 파싱 오류: {e}")
        return announcements

    def _parse_row(self, row) -> Optional[Announcement]:
        """테이블 행 파싱

        테이블 구조 (5개 셀):
        td[0]: 번호 (순번)
        td[1]: D-day (남은신청기간, 'D-15' / '종료')
        td[2]: 공고제목 + 사업명 + 신청기간 (중첩 구조)
               - a: 공고제목 (링크)
               - span.bluebox: 사업명
               - span.bco: 신청기간
        td[3]: 작성자 (담당자 이름)
        td[4]: 작성일자 ('2026-10-15')
        """
        cells = row.find_all("td")
        if len(cells) < 3:
            return None

        info_cell = cells[2]
        link = info_cell.find("a")
        if not link:
            return None

        title = _clean(link.get_text(" ", strip=True))
        href = link.get("href", "")
        if not title or not href:
            return None

        # 공고 ID 추출 (/home/2-2/{id} 형식)
        # href 해시로 대체하지 않는다 — 마크업이 바뀌면 저장 키 체계가 바뀌어 같은 공고가 다시 신규로 알림된다
        id_match = re.search(r"/home/2-2/(\d+)", href)
        if not id_match:
            logger.warning(f"NIPA 공고 ID(/home/2-2/{{id}})를 찾을 수 없어 건너뜀 (href={href}): {title}")
            return None
        ann_id = id_match.group(1)

        # 사업명 추출 (span.bluebox 또는 span.box)
        category_span = info_cell.select_one("span.bluebox, span.box")
        category = _clean(category_span.get_text(" ", strip=True)) if category_span else None

        # 신청기간 추출 (span.bco)
        period_span = info_cell.select_one("span.bco")
        period_text = period_span.get_text(" ", strip=True) if period_span else ""
        deadline = self._parse_deadline(period_text)

        # D-day: 마감일 기준 KST 계산 (마감일이 없을 때만 목록의 'D-n' 표기 사용)
        if deadline:
            d_day = calculate_d_day(deadline, today_kst())
        else:
            d_day = self._parse_d_day(cells[1].get_text(strip=True))

        # 작성자 (td[3]) - 팀/부서명이면 사용, 사람 이름이면 "NIPA" 기본값
        raw_author = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        if re.search(r"(팀|부|실|센터|단|본부|처|국|과)$", raw_author):
            organization = raw_author
        else:
            organization = "NIPA"

        # 작성일자 (td[4])
        posted_date = None
        if len(cells) > 4:
            try:
                posted_date = datetime.strptime(cells[4].get_text(strip=True), "%Y-%m-%d")
            except ValueError:
                pass

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=d_day,
            department="정보통신산업진흥원",
            organization=organization,
            url=urljoin(self.config["base_url"], href),
            posted_date=posted_date,
        )

    def _parse_d_day(self, text: str) -> Optional[int]:
        """D-day 표기 파싱 ('D-14' → 14, '종료' 등 → None)"""
        if not text:
            return None

        match = re.search(r"D-(\d+)", text)
        if match:
            return int(match.group(1))

        if re.search(r"(?i)d-?day|오늘", text):
            return 0

        return None

    def _parse_deadline(self, text: str) -> Optional[datetime]:
        """마감일 파싱

        형식: "신청기간 : YYYY-MM-DD HH:MM ~ YYYY-MM-DD HH:MM"
        """
        if not text:
            return None

        # 종료일(~) 뒤의 날짜 추출
        parts = text.split("~")
        end_part = parts[-1].strip() if len(parts) >= 2 else text

        patterns = [
            (r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", True),
            (r"(\d{4})-(\d{2})-(\d{2})", False),
            (r"(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", True),
            (r"(\d{4})\.(\d{2})\.(\d{2})", False),
        ]

        for pattern, has_time in patterns:
            match = re.search(pattern, end_part)
            if match:
                try:
                    groups = match.groups()
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                    if has_time:
                        hour, minute = int(groups[3]), int(groups[4])
                        return datetime(year, month, day, hour, minute, 0)
                    return datetime(year, month, day, 23, 59, 59)
                except ValueError:
                    continue

        return None

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회 (본문 + 첨부파일)"""
        url = self.config["detail_url_template"].format(id=announcement_id)

        try:
            html = await self._get(url)
        except requests.RequestException as e:
            status = e.response.status_code if e.response is not None else None
            logger.warning(f"NIPA 상세 조회 실패 ({announcement_id}, HTTP {status}): {e}")
            return None

        detail = self.parse_detail_page(html)
        if detail is None:
            logger.warning(f"NIPA 상세 페이지에서 본문을 찾을 수 없음 ({announcement_id})")
        return detail

    def parse_detail_page(self, html: str) -> Optional[dict]:
        """상세 페이지 HTML 파싱

        table.tb05 행: td.tbTit(제목) / td.ggtbInfo(사업구분·신청기간·작성자·작성일) /
        td[0]=='내용' → td[1] 본문 / td[0]=='첨부파일' → 첨부 링크

        Returns:
            {'content', 'attachments'} 또는 None (상세 테이블 없음)
        """
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("div.tbWrap.gonggo.detail table.tb05") or soup.select_one("table.tb05")
        attachments = self._parse_attachments(soup)
        if table is None:
            return {"content": "", "attachments": attachments} if attachments else None

        parts = []
        title_el = table.select_one("td.tbTit")
        if title_el:
            parts.append(_clean(title_el.get_text(" ", strip=True)))

        # 사업구분 / 신청기간 (작성자·조회수는 제외)
        for info in table.select("td.ggtbInfo div.ggdtnfo"):
            text = _clean(info.get_text(" ", strip=True))
            if text:
                parts.append(text)

        body = None
        tbody = table.find("tbody", recursive=False) or table
        for tr in tbody.find_all("tr", recursive=False):
            cells = tr.find_all("td", recursive=False)
            if len(cells) > 1 and _clean(cells[0].get_text(" ", strip=True)) == "내용":
                body = _clean(cells[1].get_text(" ", strip=True))
                break

        if body is None:
            # '내용' 행을 못 찾으면 테이블 전체 텍스트
            body = _clean(table.get_text(" ", strip=True))
            parts = []
        if body:
            parts.append(body)

        return {
            "content": "\n".join(parts)[:5000],
            "attachments": attachments,
        }

    def _parse_attachments(self, soup: BeautifulSoup) -> list[dict]:
        """첨부파일: a[href*='/comm/getFile'] (이름 끝의 '(파일크기: N KB )' 제거)"""
        attachments = []
        seen_urls = set()
        for a in soup.select("a[href*='/comm/getFile']"):
            name = FILE_SIZE_SUFFIX.sub("", _clean(a.get_text(" ", strip=True)))
            url = urljoin(self.config["base_url"], a["href"].strip())
            if not name or url in seen_urls:
                continue
            seen_urls.add(url)
            attachments.append({"name": name, "url": url})
        return sort_attachments(attachments)

    async def close(self):
        """세션 종료"""
        self.session.close()
