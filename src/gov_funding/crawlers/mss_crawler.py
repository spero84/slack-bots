"""중소벤처기업부(MSS) 크롤러 - requests 기반

NIA와 동일한 BBS 시스템 (doBbsFView 패턴).
서버사이드 렌더링이므로 requests + BeautifulSoup으로 처리.
"""
import hashlib
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler


class MssCrawler(BaseCrawler):
    """중소벤처기업부 크롤러 (서버사이드 렌더링)"""

    source = Source.MSS
    name = "MSS"

    def __init__(self):
        self.config = CRAWL_SOURCES["mss"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 목록 크롤링"""
        announcements = []
        page = 1

        while len(announcements) < max_items:
            try:
                items = self._crawl_page(page)
                if not items:
                    break

                new_items = [i for i in items if i.id not in {a.id for a in announcements}]
                announcements.extend(new_items)

                logger.info(f"MSS 페이지 {page}: {len(new_items)}건 추가 (총 {len(announcements)}건)")

                if len(items) < 10:
                    break

                page += 1
            except Exception as e:
                logger.error(f"MSS 페이지 {page} 크롤링 오류: {e}")
                break

        result = announcements[:max_items]
        logger.info(f"MSS 크롤링 완료: 총 {len(result)}건")
        return result

    def _crawl_page(self, page: int) -> list[Announcement]:
        """단일 페이지 크롤링"""
        params = {"pageIndex": page}

        response = self.session.get(self.config["list_url"], params=params, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        announcements = []

        # MSS BBS: NIA와 동일한 doBbsFView 패턴
        # ul > li > a 구조 또는 table > tbody > tr 구조
        for li in soup.select("ul li"):
            try:
                ann = self._parse_item_li(li)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"MSS li 파싱 오류: {e}")

        # li에서 못 찾으면 table 구조 시도
        if not announcements:
            for tr in soup.select("table tbody tr"):
                try:
                    ann = self._parse_item_tr(tr)
                    if ann:
                        announcements.append(ann)
                except Exception as e:
                    logger.debug(f"MSS tr 파싱 오류: {e}")

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

        # 공고번호 추출 (제2026-110호 등)
        notice_num_match = re.search(r'제?\d{4}-\d+호?', title)

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
                delta = (deadline - datetime.now()).days
                d_day = delta if delta >= 0 else None
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
        """<tr> 아이템 파싱 (테이블 구조 대체)"""
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

        # ID 추출
        ann_id = None
        onclick = link.get("onclick", "")
        href = link.get("href", "")

        view_match = re.search(
            r"doBbsFView\s*\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,\s*'?([^']*)'?\s*,\s*'?(\d+)'?\s*\)",
            onclick
        )
        if view_match:
            ann_id = view_match.group(2)
            parentSeq = view_match.group(4)
        else:
            bcIdx_match = re.search(r'bcIdx=(\d+)', href)
            if bcIdx_match:
                ann_id = bcIdx_match.group(1)
            parentSeq = "0"

        if not ann_id:
            ann_id = hashlib.md5(title.encode()).hexdigest()[:12]

        # 날짜 추출 (보통 마지막 또는 마지막에서 2번째 셀)
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
            parentSeq=parentSeq if 'parentSeq' in dir() else "0",
        )

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=None,
            deadline=None,
            d_day=None,
            department="중소벤처기업부",
            organization="MSS",
            url=url,
            posted_date=posted_date,
        )

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회"""
        url = self.config["detail_url_template"].format(
            bcIdx=announcement_id,
            parentSeq="0",
        )

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")

            # 상세 내용 추출
            content_div = soup.select_one(
                "div.view_cont, div.content, div.board_view, "
                "div.bbs_view, div.bbsV_cont"
            )
            content = content_div.get_text(strip=True) if content_div else ""

            # 첨부파일 목록
            attachments = []
            for a in soup.select("a[href*='download'], a[href*='fileDown'], a.file_down"):
                name = a.get_text(strip=True)
                href = a.get("href", "")
                if name and href:
                    if not href.startswith("http"):
                        href = self.config["base_url"] + href
                    attachments.append({"name": name, "url": href})

            return {
                "content": content[:5000],
                "attachments": attachments,
            }
        except Exception as e:
            logger.error(f"MSS 상세 조회 오류 ({announcement_id}): {e}")
            return None

    async def close(self):
        """세션 종료"""
        self.session.close()
