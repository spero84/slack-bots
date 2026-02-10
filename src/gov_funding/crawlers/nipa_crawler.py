"""NIPA(정보통신산업진흥원) 크롤러 - requests 기반"""
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler


class NipaCrawler(BaseCrawler):
    """NIPA 크롤러 (서버사이드 렌더링)"""

    source = Source.NIPA
    name = "NIPA"

    def __init__(self):
        self.config = CRAWL_SOURCES["nipa"]
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
                items = await self._crawl_page(page)
                if not items:
                    break

                announcements.extend(items)
                logger.info(f"NIPA 페이지 {page} 크롤링 완료: {len(items)}건")

                page += 1
            except Exception as e:
                logger.error(f"NIPA 크롤링 오류 (페이지 {page}): {e}")
                break

        result = announcements[:max_items]
        logger.info(f"NIPA 크롤링 완료: 총 {len(result)}건")
        return result

    async def _crawl_page(self, page: int) -> list[Announcement]:
        """단일 페이지 크롤링"""
        params = {"curPage": page}

        response = self.session.get(self.config["list_url"], params=params, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        announcements = []

        # 테이블 구조: div.board_list 내 테이블
        table = soup.find("table")
        if not table:
            logger.warning("NIPA 테이블을 찾을 수 없음")
            return []

        tbody = table.find("tbody")
        if not tbody:
            logger.warning("NIPA tbody를 찾을 수 없음")
            return []

        rows = tbody.find_all("tr")
        for row in rows:
            try:
                ann = self._parse_row(row)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"NIPA 행 파싱 오류: {e}")
                continue

        return announcements

    def _parse_row(self, row) -> Optional[Announcement]:
        """테이블 행 파싱

        테이블 구조 (5개 셀):
        td[0]: 번호 (순번)
        td[1]: D-day (남은신청기간)
        td[2]: 공고제목 + 사업명 + 신청기간 (중첩 구조)
               - a: 공고제목 (링크)
               - span.bluebox: 사업명
               - span.bco: 신청기간
        td[3]: 작성자
        td[4]: 작성일자
        """
        cells = row.find_all("td")
        if len(cells) < 3:
            return None

        # D-day 추출 (td[1])
        d_day_text = cells[1].get_text(strip=True)
        d_day = self._parse_d_day(d_day_text)

        # td[2]에서 제목, 사업명, 신청기간 추출
        info_cell = cells[2]

        # 제목 및 링크 추출
        link = info_cell.find("a")
        if not link:
            return None

        title = link.get_text(strip=True)
        if not title:
            return None

        href = link.get("href", "")
        if not href:
            return None

        # 공고 ID 추출 (/home/2-2/{id} 형식)
        id_match = re.search(r"/home/2-2/(\d+)", href)
        if id_match:
            ann_id = id_match.group(1)
        else:
            import hashlib
            ann_id = hashlib.md5(href.encode()).hexdigest()[:12]

        # 사업명 추출 (span.bluebox 또는 span.box)
        category_span = info_cell.select_one("span.bluebox, span.box")
        category = category_span.get_text(strip=True) if category_span else None

        # 신청기간 추출 (span.bco)
        period_span = info_cell.select_one("span.bco")
        period_text = period_span.get_text(strip=True) if period_span else ""
        deadline = self._parse_deadline(period_text)

        # D-day가 없고 deadline이 있으면 계산
        if d_day is None and deadline:
            delta = (deadline - datetime.now()).days
            d_day = delta if delta >= 0 else None

        # 작성자 (td[3]) - 팀/부서명이면 사용, 사람 이름이면 "NIPA" 기본값
        raw_author = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        if re.search(r'(팀|부|실|센터|단|본부|처|국|과)$', raw_author):
            organization = raw_author
        else:
            organization = "NIPA"

        # 상세 URL 생성
        if href.startswith("/"):
            url = self.config["base_url"] + href
        else:
            url = href

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=d_day,
            department="정보통신산업진흥원",
            organization=organization,
            url=url,
        )

    def _parse_d_day(self, text: str) -> Optional[int]:
        """D-day 파싱"""
        if not text:
            return None

        # "D-14", "D-324" 형식
        match = re.search(r"D-(\d+)", text)
        if match:
            return int(match.group(1))

        # "마감" 또는 기타
        if "마감" in text:
            return 0

        return None

    def _parse_deadline(self, text: str) -> Optional[datetime]:
        """마감일 파싱

        형식: "YYYY-MM-DD HH:MM ~ YYYY-MM-DD HH:MM"
        """
        if not text:
            return None

        # 종료일(~) 뒤의 날짜 추출
        # 예: "2025-02-10 09:00 ~ 2025-02-24 18:00"
        parts = text.split("~")
        if len(parts) >= 2:
            end_part = parts[-1].strip()
        else:
            end_part = text

        # 날짜+시간 파싱
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
                    else:
                        return datetime(year, month, day, 23, 59, 59)
                except ValueError:
                    continue

        return None

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회"""
        url = self.config["detail_url_template"].format(id=announcement_id)

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")

            # 상세 내용 추출
            content_div = soup.select_one("div.view_cont, div.content, div.board_view")
            content = content_div.get_text(strip=True) if content_div else ""

            # 첨부파일 목록
            attachments = []
            for link in soup.select("a[href*='download'], a.file_down, a[href*='fileDown']"):
                attachments.append({
                    "name": link.get_text(strip=True),
                    "url": link.get("href", ""),
                })

            return {
                "content": content[:5000],
                "attachments": attachments,
            }
        except Exception as e:
            logger.error(f"NIPA 상세 조회 오류 ({announcement_id}): {e}")
            return None

    async def close(self):
        """세션 종료"""
        self.session.close()
