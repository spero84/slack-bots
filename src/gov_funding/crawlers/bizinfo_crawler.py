"""기업마당(Bizinfo) 크롤러 - requests 기반"""
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler


class BizinfoCrawler(BaseCrawler):
    """기업마당 크롤러 (서버사이드 렌더링)"""

    source = Source.BIZINFO
    name = "기업마당"

    def __init__(self):
        self.config = CRAWL_SOURCES["bizinfo"]
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
        per_page = 20

        while len(announcements) < max_items:
            try:
                items = await self._crawl_page(page, per_page)
                if not items:
                    break

                announcements.extend(items)
                logger.info(f"Bizinfo 페이지 {page} 크롤링 완료: {len(items)}건")

                if len(items) < per_page:
                    break

                page += 1
            except Exception as e:
                logger.error(f"Bizinfo 크롤링 오류 (페이지 {page}): {e}")
                break

        result = announcements[:max_items]
        logger.info(f"Bizinfo 크롤링 완료: 총 {len(result)}건")
        return result

    async def _crawl_page(self, page: int, per_page: int) -> list[Announcement]:
        """단일 페이지 크롤링"""
        # 기업마당 목록 페이지 요청
        params = {
            "rows": per_page,
            "cpage": page,
        }

        response = self.session.get(self.config["list_url"], params=params, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        announcements = []

        # 첫 번째 테이블이 공고 목록 (class 없음)
        table = soup.find("table")
        if not table:
            logger.warning("Bizinfo 테이블을 찾을 수 없음")
            return []

        # 헤더 행 제외하고 데이터 행만 파싱
        rows = table.find_all("tr")[1:]  # 첫 번째 행은 헤더
        for row in rows:
            try:
                ann = self._parse_row(row)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"행 파싱 오류: {e}")
                continue

        return announcements

    def _parse_row(self, row) -> Optional[Announcement]:
        """테이블 행 파싱

        테이블 구조:
        td[0]: 번호
        td[1]: 지원분야
        td[2]: 지원사업명 (링크 포함)
        td[3]: 신청기간
        td[4]: 소관부처·지자체
        td[5]: 사업수행기관
        """
        cells = row.find_all("td")
        if len(cells) < 6:
            return None

        # 제목 및 링크 추출 (td[2])
        title_cell = cells[2]
        link = title_cell.find("a")
        if not link:
            return None

        title = link.get_text(strip=True)
        if not title:
            return None

        href = link.get("href", "")
        if not href:
            return None

        # 공고 ID 추출 (URL의 hashCode 또는 전체 URL 해시)
        # 실제 URL: /sii/siia/selectSIIA200Detail.do?hashCode=...
        # ID로 hashCode 파라미터나 URL 자체를 해시해서 사용
        hash_match = re.search(r"hashCode=([^&]+)", href)
        if hash_match:
            ann_id = hash_match.group(1)
        else:
            # URL 자체를 ID로 사용 (해시)
            import hashlib
            ann_id = hashlib.md5(href.encode()).hexdigest()[:12]

        # 카테고리/분야 (td[1])
        category = cells[1].get_text(strip=True) if len(cells) > 1 else None

        # 신청기간 (td[3]) - 마감일 추출
        period_text = cells[3].get_text(strip=True) if len(cells) > 3 else ""
        deadline = self._parse_deadline(period_text)

        # D-day 계산
        d_day = None
        if deadline:
            delta = (deadline - datetime.now()).days
            d_day = delta if delta >= 0 else None

        # 소관부처 (td[4])
        department = cells[4].get_text(strip=True) if len(cells) > 4 else None

        # 주관기관 (td[5])
        organization = cells[5].get_text(strip=True) if len(cells) > 5 else None

        # 상세 URL 생성 (상대 경로를 절대 경로로)
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
            department=department,
            organization=organization,
            url=url,
        )

    def _parse_deadline(self, text: str) -> Optional[datetime]:
        """마감일 파싱"""
        if not text or text in ["상시", "예산소진시"]:
            return None

        # 다양한 날짜 포맷 처리
        patterns = [
            r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})",
            r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    year, month, day = map(int, match.groups())
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
            content_div = soup.select_one("div.view_cont, div.content, div.bbs_view")
            content = content_div.get_text(strip=True) if content_div else ""

            # 첨부파일 목록
            attachments = []
            for link in soup.select("a[href*='download'], a.file_down"):
                attachments.append({
                    "name": link.get_text(strip=True),
                    "url": link.get("href", ""),
                })

            return {
                "content": content[:5000],  # 최대 5000자
                "attachments": attachments,
            }
        except Exception as e:
            logger.error(f"Bizinfo 상세 조회 오류 ({announcement_id}): {e}")
            return None

    async def close(self):
        """세션 종료"""
        self.session.close()
