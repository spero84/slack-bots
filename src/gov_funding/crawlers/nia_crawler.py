"""NIA(한국지능정보사회진흥원) 크롤러 - requests 기반

두 개 게시판 동시 크롤링:
- 입찰공고 (cbIdx=78336)
- 사업공고 (cbIdx=99835)
"""
import hashlib
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler


class NiaCrawler(BaseCrawler):
    """NIA 크롤러 (서버사이드 렌더링, 두 게시판)"""

    source = Source.NIA
    name = "NIA"

    def __init__(self):
        self.config = CRAWL_SOURCES["nia"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """두 게시판에서 공고 크롤링"""
        all_announcements = []
        boards = self.config["boards"]
        per_board_max = max_items // len(boards)

        for board in boards:
            try:
                board_items = await self._crawl_board(board, per_board_max)
                all_announcements.extend(board_items)
                logger.info(f"NIA {board['name']} 크롤링 완료: {len(board_items)}건")
            except Exception as e:
                logger.error(f"NIA {board['name']} 크롤링 오류: {e}")

        result = all_announcements[:max_items]
        logger.info(f"NIA 크롤링 완료: 총 {len(result)}건")
        return result

    async def _crawl_board(self, board: dict, max_items: int) -> list[Announcement]:
        """단일 게시판 크롤링"""
        announcements = []
        page = 1

        while len(announcements) < max_items:
            try:
                items = await self._crawl_page(board, page)
                if not items:
                    break

                announcements.extend(items)
                logger.info(f"NIA {board['name']} 페이지 {page}: {len(items)}건")

                if len(items) < 10:  # 페이지당 10건
                    break

                page += 1
            except Exception as e:
                logger.error(f"NIA {board['name']} 페이지 {page} 오류: {e}")
                break

        return announcements[:max_items]

    async def _crawl_page(self, board: dict, page: int) -> list[Announcement]:
        """단일 페이지 크롤링"""
        params = {"pageIndex": page}

        response = self.session.get(board["list_url"], params=params, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        announcements = []

        # NIA 게시판: ul > li > a 구조
        # 게시판 목록 영역의 li 태그들을 찾기
        # 공지사항 고정글과 일반글이 모두 포함됨
        for li in soup.select("ul li"):
            try:
                ann = self._parse_item(li, board)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"NIA 아이템 파싱 오류: {e}")

        return announcements

    def _parse_item(self, li, board: dict) -> Optional[Announcement]:
        """<li> 아이템 파싱

        구조:
        <li>
          <a href="#view" onclick="doBbsFView('cbIdx','bcIdx','Gbn','parentSeq')" title="[카테고리] 제목">
            [카테고리] 제목
            <span class="ico_file">첨부파일 있음</span>
            <span class="ico_new">new</span>
            <span class="date">2026.02.09</span>
            <span class="hit">조회수 232</span>
            <span class="writer">작성자</span>
            <span class="dept">부서명</span>
          </a>
        </li>
        """
        link = li.find("a")
        if not link:
            return None

        # onclick 속성에서 bcIdx 추출
        onclick = link.get("onclick", "")
        bcIdx = None
        parentSeq = "0"

        # doBbsFView('78336','12345','A','0') 패턴
        view_match = re.search(
            r"doBbsFView\s*\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?\s*,\s*'?([^']*)'?\s*,\s*'?(\d+)'?\s*\)",
            onclick
        )
        if view_match:
            bcIdx = view_match.group(2)
            parentSeq = view_match.group(4)
        else:
            # onclick이 없거나 패턴이 다른 경우 스킵 (네비게이션 링크 등)
            return None

        # 제목 추출: title 속성 또는 텍스트
        title_attr = link.get("title", "")
        # title 속성에서 "-첨부파일 있음" 접미사 제거
        raw_title = re.sub(r'-첨부파일\s*(있음|없음)$', '', title_attr).strip()
        # "(새 글)" 제거
        raw_title = re.sub(r'\(새\s*글\)$', '', raw_title).strip()

        if not raw_title:
            return None

        # [카테고리] prefix 분리
        cat_match = re.match(r'\[([^\]]+)\]\s*', raw_title)
        if cat_match:
            category = cat_match.group(1)
            title = raw_title[cat_match.end():].strip()
        else:
            category = board["name"]
            title = raw_title

        if not title:
            return None

        # 부서명 추출 (span 중 마지막 의미있는 텍스트)
        organization = None
        spans = link.find_all("span")
        for span in spans:
            text = span.get_text(strip=True)
            # 팀/센터/부 등으로 끝나는 span은 부서
            if re.search(r'(팀|부|실|센터|단|본부|처|국|과|TF)$', text):
                organization = text

        # 상세 URL 생성
        url = self.config["detail_url_template"].format(
            cbIdx=board["cbIdx"],
            bcIdx=bcIdx,
            parentSeq=parentSeq,
        )

        return Announcement(
            id=bcIdx,
            source=self.source,
            title=title,
            category=category,
            deadline=None,  # NIA 목록에 마감일 정보 없음
            d_day=None,
            department="한국지능정보사회진흥원",
            organization=organization or "NIA",
            url=url,
        )

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회"""
        # bcIdx로 상세 페이지 조회 (cbIdx는 알 수 없으므로 두 게시판 모두 시도)
        for board in self.config["boards"]:
            url = self.config["detail_url_template"].format(
                cbIdx=board["cbIdx"],
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

                if content:
                    # 첨부파일 목록
                    attachments = []
                    for a in soup.select("a[href*='download'], a[href*='fileDown'], a.file_down"):
                        attachments.append({
                            "name": a.get_text(strip=True),
                            "url": a.get("href", ""),
                        })

                    return {
                        "content": content[:5000],
                        "attachments": attachments,
                    }
            except Exception as e:
                logger.debug(f"NIA 상세 조회 시도 ({board['name']}, {announcement_id}): {e}")

        logger.error(f"NIA 상세 조회 실패 ({announcement_id})")
        return None

    async def close(self):
        """세션 종료"""
        self.session.close()
