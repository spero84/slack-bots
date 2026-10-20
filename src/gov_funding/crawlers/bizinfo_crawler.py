"""기업마당(Bizinfo) 크롤러 - requests 기반

목록: https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do (서버사이드 렌더링)
- 페이지당 15건 고정 (rows 파라미터는 무시됨), cpage로 페이지 이동
- 등록일 내림차순 정렬, 영업일 기준 하루 30~40건 등록
- 공고 ID는 href의 pblancId (hashCode 파라미터는 항상 비어 있음)
"""
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from ..utils.dates import calculate_d_day, today_kst
from .base_crawler import BaseCrawler

# 사업수행기관 컬럼에 기관명 대신 들어가는 분류값 → 소관부처·지자체로 대체
PLACEHOLDER_ORGANIZATIONS = {"", "기초자치단체", "직접수행"}

# 날짜: 2026-10-26 / 2026.10.26 / 20261026
DATE_PATTERN = re.compile(r"(\d{4})[.\-/]?\s*(\d{1,2})[.\-/]?\s*(\d{1,2})(?!\d)")


def parse_deadline(text: str) -> Optional[datetime]:
    """신청기간 텍스트에서 마감일 추출

    '2026-10-16 ~ 2026-10-26'처럼 기간이 주어지면 마지막 날짜(종료일)를 마감일로 본다.
    '예산 소진시까지', '상시 접수', '모집 완료시까지' 등 날짜가 없으면 None.
    '2026-10-01 ~'처럼 시작일만 있으면 마감일을 알 수 없으므로 None.
    """
    if not text:
        return None

    text = text.strip()
    dates = []
    for match in DATE_PATTERN.finditer(text):
        try:
            year, month, day = map(int, match.groups())
            dates.append(datetime(year, month, day, 23, 59, 59))
        except ValueError:
            continue

    if not dates:
        return None
    if len(dates) == 1 and text.endswith("~"):
        return None
    return dates[-1]


class BizinfoCrawler(BaseCrawler):
    """기업마당 크롤러 (서버사이드 렌더링)"""

    source = Source.BIZINFO
    name = "기업마당"

    def __init__(
        self,
        lookback_days: Optional[int] = None,
        max_pages: Optional[int] = None,
        page_delay: float = 1.0,
    ):
        self.config = CRAWL_SOURCES["bizinfo"]
        self.lookback_days = lookback_days if lookback_days is not None else self.config["lookback_days"]
        self.max_pages = max_pages if max_pages is not None else self.config["max_pages"]
        self.page_delay = page_delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    async def crawl(self, max_items: int = 300) -> list[Announcement]:
        """공고 목록 크롤링

        등록일 기준 최근 lookback_days일 이내 공고를 수집한다.
        등록일이 기준일보다 오래된 행이 나오면 다음 페이지는 요청하지 않는다.
        """
        cutoff = today_kst() - timedelta(days=self.lookback_days)
        announcements: list[Announcement] = []
        seen_ids: set[str] = set()
        page = 1

        while len(announcements) < max_items and page <= self.max_pages:
            try:
                items, last_page = self._crawl_page(page)
            except Exception as e:
                logger.warning(f"Bizinfo 크롤링 오류 (페이지 {page}): {e}")
                break

            if not items:
                break

            reached_cutoff = False
            for ann in items:
                if ann.posted_date and ann.posted_date.date() < cutoff:
                    reached_cutoff = True
                    continue
                if ann.id in seen_ids:
                    continue
                seen_ids.add(ann.id)
                announcements.append(ann)

            logger.info(f"Bizinfo 페이지 {page} 크롤링 완료: {len(items)}건 (누적 {len(announcements)}건)")

            if reached_cutoff or (last_page is not None and page >= last_page):
                break

            page += 1
            if self.page_delay:
                await asyncio.sleep(self.page_delay)

        result = announcements[:max_items]
        if not result:
            logger.warning("Bizinfo 크롤링 결과 0건 - 사이트 구조 변경 여부 확인 필요")
        else:
            logger.info(f"Bizinfo 크롤링 완료: 총 {len(result)}건 (등록일 {cutoff} 이후)")
        return result

    def _crawl_page(self, page: int) -> tuple[list[Announcement], Optional[int]]:
        """단일 페이지 크롤링

        Returns:
            (공고 목록, 마지막 페이지 번호)
        """
        params = {"schEndAt": "N", "cpage": page}
        response = self.session.get(self.config["list_url"], params=params, timeout=30)
        response.raise_for_status()
        return self.parse_list_page(response.text)

    def parse_list_page(self, html: str) -> tuple[list[Announcement], Optional[int]]:
        """목록 페이지 HTML 파싱"""
        soup = BeautifulSoup(html, "lxml")

        table = soup.find("table")
        if not table:
            logger.warning("Bizinfo 테이블을 찾을 수 없음")
            return [], None

        announcements = []
        for row in table.select("tbody tr"):
            try:
                ann = self._parse_row(row)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"행 파싱 오류: {e}")

        last_page = None
        last_link = soup.select_one(".page_wrap a[title=마지막페이지]")
        if last_link:
            match = re.search(r"cpage=(\d+)", last_link.get("href", ""))
            if match:
                last_page = int(match.group(1))

        return announcements, last_page

    def _parse_row(self, row) -> Optional[Announcement]:
        """테이블 행 파싱

        td[0]: 번호, td[1]: 지원분야, td[2]: 지원사업명(링크), td[3]: 신청기간,
        td[4]: 소관부처·지자체, td[5]: 사업수행기관, td[6]: 등록일, td[7]: 조회수
        """
        cells = row.find_all("td")
        if len(cells) < 7:
            return None

        link = cells[2].find("a")
        if not link:
            return None

        title = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
        id_match = re.search(r"pblancId=(PBLN_\d+)", link.get("href", ""))
        if not title or not id_match:
            return None
        ann_id = id_match.group(1)

        category = cells[1].get_text(strip=True) or None
        deadline = parse_deadline(cells[3].get_text(" ", strip=True))
        department = cells[4].get_text(strip=True) or None

        organization = cells[5].get_text(strip=True)
        if organization in PLACEHOLDER_ORGANIZATIONS:
            organization = department

        posted_date = None
        try:
            posted_date = datetime.strptime(cells[6].get_text(strip=True), "%Y-%m-%d")
        except ValueError:
            pass

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=calculate_d_day(deadline, today_kst()),
            department=department,
            organization=organization,
            url=self.config["detail_url_template"].format(id=ann_id),
            posted_date=posted_date,
        )

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회 (사업개요 등 본문 + 첨부파일)"""
        url = self.config["detail_url_template"].format(id=announcement_id)

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return self.parse_detail_page(response.text)
        except Exception as e:
            logger.warning(f"Bizinfo 상세 조회 오류 ({announcement_id}): {e}")
            return None

    def parse_detail_page(self, html: str) -> Optional[dict]:
        """상세 페이지 HTML 파싱"""
        soup = BeautifulSoup(html, "lxml")

        fields = {}
        for li in soup.select("div.view_cont > ul > li"):
            label = li.select_one("span.s_title")
            value = li.select_one("div.txt")
            if label and value:
                fields[label.get_text(strip=True)] = value.get_text("\n", strip=True)

        if not fields:
            logger.warning("Bizinfo 상세 페이지에서 본문을 찾을 수 없음")
            return None

        content = "\n".join(f"[{label}] {value}" for label, value in fields.items())

        return {
            "content": content[:5000],
            "attachments": self._parse_attachments(soup),
            "fields": fields,
        }

    def _parse_attachments(self, soup: BeautifulSoup) -> list[dict]:
        """첨부파일 목록 추출

        '본문출력파일'(공고문 원본)을 가장 앞에, 이름에 '공고'가 들어간 파일을 그다음에 둔다.
        Bedrock 분석은 지원 형식(hwp/hwpx/pdf/docx)의 첫 번째 파일만 읽기 때문이다.
        한 목록 안에 '첨부파일'과 '본문출력파일' 제목이 섞여 있을 수 있어 항목마다 직전 제목으로 구분한다.
        """
        attachments = []
        for block in soup.select("div.attached_file_list"):
            kind = ""
            for el in block.find_all(["h3", "li"]):
                if el.name == "h3":
                    kind = el.get_text(strip=True)
                    continue
                name_el = el.select_one(".file_name")
                link = el.select_one("a.icon_download[href]")
                if not name_el or not link:
                    continue
                attachments.append({
                    "name": name_el.get_text(strip=True),
                    "url": urljoin(self.config["base_url"], link["href"]),
                    "kind": kind,
                })

        def priority(att: dict) -> int:
            if att["kind"] == "본문출력파일":
                return 0
            if "공고" in att["name"]:
                return 1
            return 2

        return sorted(attachments, key=priority)

    async def close(self):
        """세션 종료"""
        self.session.close()
