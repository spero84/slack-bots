"""JOINTIPS(조인팁스) 크롤러 - requests 기반

순차 ID 크롤링: wr_id=2000부터 1씩 증가하며 상세 페이지 직접 방문.
연속 N회 빈 페이지 시 크롤링 중단.
"""
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler


class JointipsCrawler(BaseCrawler):
    """JOINTIPS 크롤러 (순차 ID 방식)"""

    source = Source.JOINTIPS
    name = "JOINTIPS"

    # 연속 빈 페이지 허용 횟수
    MAX_CONSECUTIVE_MISSES = 20

    def __init__(self):
        self.config = CRAWL_SOURCES["jointips"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """순차 ID 크롤링

        wr_id를 start_wr_id부터 1씩 증가하며 상세 페이지를 방문.
        연속 MAX_CONSECUTIVE_MISSES회 빈 페이지면 중단.
        """
        announcements = []
        wr_id = self.config.get("start_wr_id", 2000)
        consecutive_misses = 0

        while len(announcements) < max_items:
            try:
                ann = await self._crawl_detail(wr_id)
                if ann:
                    announcements.append(ann)
                    consecutive_misses = 0
                    logger.debug(f"JOINTIPS wr_id={wr_id} 크롤링 성공: {ann.title}")
                else:
                    consecutive_misses += 1
                    if consecutive_misses >= self.MAX_CONSECUTIVE_MISSES:
                        logger.info(
                            f"JOINTIPS 연속 {self.MAX_CONSECUTIVE_MISSES}회 빈 페이지 - "
                            f"크롤링 중단 (마지막 wr_id={wr_id})"
                        )
                        break
            except Exception as e:
                logger.debug(f"JOINTIPS wr_id={wr_id} 오류: {e}")
                consecutive_misses += 1
                if consecutive_misses >= self.MAX_CONSECUTIVE_MISSES:
                    logger.info(f"JOINTIPS 연속 오류로 크롤링 중단 (wr_id={wr_id})")
                    break

            wr_id += 1

        logger.info(f"JOINTIPS 크롤링 완료: 총 {len(announcements)}건")
        return announcements

    async def _crawl_detail(self, wr_id: int) -> Optional[Announcement]:
        """단일 상세 페이지 크롤링"""
        url = self.config["detail_url_template"].format(id=wr_id)

        response = self.session.get(url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")

        # 오류 페이지 감지: "글이 존재하지 않습니다"
        if "글이 존재하지 않습니다" in response.text:
            return None

        # 제목 추출: <title> 태그에서 " > 이벤트관리 | TIPS" 제거
        title = self._parse_title(soup)
        if not title:
            return None

        # 카테고리 추출: 제목 h2 바로 앞의 카테고리 링크
        category = self._parse_category(soup)

        # 메타데이터 추출
        page_text = soup.get_text()
        deadline = self._parse_deadline(page_text)
        d_day = self._calc_d_day(deadline)
        organization = self._parse_organization(page_text)

        # 본문 요약용 텍스트
        summary_text = self._extract_content(soup)

        return Announcement(
            id=str(wr_id),
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=d_day,
            department="TIPS",
            organization=organization,
            url=url,
            summary=summary_text[:200] if summary_text else None,
        )

    def _parse_title(self, soup: BeautifulSoup) -> Optional[str]:
        """제목 추출

        <title> 태그에서 추출: "{제목} > 이벤트관리 | TIPS"
        """
        title_tag = soup.find("title")
        if title_tag:
            raw = title_tag.get_text(strip=True)
            match = re.match(r"(.+?)\s*>\s*이벤트관리", raw)
            if match:
                title = match.group(1).strip()
                if title and "오류" not in title:
                    return title

        # fallback: 두 번째 h2 태그 (첫 번째는 "PROGRAM" 섹션 헤더)
        h2_tags = soup.find_all("h2")
        for h2 in h2_tags:
            text = h2.get_text(strip=True)
            if text and text != "PROGRAM" and "오류" not in text:
                return text

        return None

    def _parse_category(self, soup: BeautifulSoup) -> Optional[str]:
        """카테고리 추출

        제목 h2 바로 앞의 카테고리 링크를 찾는다.
        카테고리 링크 패턴: <a href="...sfl=wr_3&stx=...">교육</a>
        """
        # 제목 h2 (PROGRAM이 아닌) 바로 앞의 카테고리 링크
        h2_tags = soup.find_all("h2")
        for h2 in h2_tags:
            text = h2.get_text(strip=True)
            if text and text != "PROGRAM":
                # h2 앞의 형제 요소에서 카테고리 링크 검색
                link = h2.find_previous("a", href=re.compile(r"sfl=wr_3&stx="))
                if link:
                    return link.get_text(strip=True)
                break
        return None

    def _parse_deadline(self, text: str) -> Optional[datetime]:
        """접수기간에서 마감일 추출

        형식: "접수기간 : 2026.01.12 2:00pm ~ 2026.01.29 2:00pm"
        또는: "접수기간 :\n2026.01.12 12:00am ~ 2026.01.29 11:30pm"
        """
        # "접수기간" 뒤 ~를 포함한 날짜 범위 추출 (줄바꿈 허용)
        match = re.search(
            r"접수기간\s*:\s*\n?\s*(.+?~.+?)(?:\n|$)",
            text,
        )
        if not match:
            return None

        period_text = match.group(1).strip()

        # "~" 뒤의 종료일 추출
        parts = period_text.split("~")
        if len(parts) < 2:
            return None

        end_part = parts[-1].strip()
        return self._parse_date(end_part)

    def _parse_date(self, text: str) -> Optional[datetime]:
        """날짜 문자열 파싱

        지원 형식:
        - 2026.01.29 2:00pm
        - 2026.01.29 14:00
        - 2026.01.29
        - 2026-01-29
        - 2026. 01. 29. (공백 포함)
        """
        # "YYYY.MM.DD H:MMam/pm" 형식
        match = re.search(r"(\d{4})[.\-]\s*(\d{1,2})[.\-]\s*(\d{1,2})\.?\s+(\d{1,2}):(\d{2})\s*(am|pm|AM|PM)", text)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            hour, minute = int(match.group(4)), int(match.group(5))
            ampm = match.group(6).lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            try:
                return datetime(year, month, day, hour, minute, 0)
            except ValueError:
                pass

        # "YYYY.MM.DD HH:MM" 형식 (24시간)
        match = re.search(r"(\d{4})[.\-]\s*(\d{1,2})[.\-]\s*(\d{1,2})\.?\s+(\d{1,2}):(\d{2})", text)
        if match:
            try:
                return datetime(
                    int(match.group(1)), int(match.group(2)), int(match.group(3)),
                    int(match.group(4)), int(match.group(5)), 0,
                )
            except ValueError:
                pass

        # "YYYY.MM.DD" 또는 "YYYY. MM. DD." 형식
        match = re.search(r"(\d{4})[.\-]\s*(\d{1,2})[.\-]\s*(\d{1,2})", text)
        if match:
            try:
                return datetime(
                    int(match.group(1)), int(match.group(2)), int(match.group(3)),
                    23, 59, 59,
                )
            except ValueError:
                pass

        return None

    def _calc_d_day(self, deadline: Optional[datetime]) -> Optional[int]:
        """D-day 계산"""
        if not deadline:
            return None
        delta = (deadline - datetime.now()).days
        return delta if delta >= 0 else 0

    def _parse_organization(self, text: str) -> Optional[str]:
        """주최/주관 추출"""
        match = re.search(r"주최/주관\s*:\s*(.+?)(?:\n|$)", text)
        if match:
            org = match.group(1).strip()
            # 불필요한 후행 텍스트 제거
            org = org.split("\n")[0].strip()
            return org if org else None
        return None

    def _extract_content(self, soup: BeautifulSoup) -> Optional[str]:
        """본문 텍스트 추출 (요약용)"""
        # bo_v_con 영역 또는 view-content 영역
        content_div = soup.select_one("#bo_v_con, .bo_v_con, .view-content")
        if content_div:
            return content_div.get_text(separator=" ", strip=True)
        return None

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회"""
        url = self.config["detail_url_template"].format(id=announcement_id)

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "lxml")
            content_div = soup.select_one("#bo_v_con, .bo_v_con, .view-content")
            content = content_div.get_text(strip=True) if content_div else ""

            return {
                "content": content[:5000],
            }
        except Exception as e:
            logger.error(f"JOINTIPS 상세 조회 오류 ({announcement_id}): {e}")
            return None

    async def close(self):
        """세션 종료"""
        self.session.close()
