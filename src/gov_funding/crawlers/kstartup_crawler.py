"""K-Startup 크롤러 - requests 기반

목록: https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do (서버사이드 렌더링)
- 무한 스크롤이 아니라 ?page=N 페이지네이션이다 (페이지당 15건, 기본 정렬은 등록일자 내림차순)
- 탭 2개: pbancClssCd=PBC010 (중앙부처·지자체·공공기관), PBC020 (민간기관·교육기관).
  두 탭의 공고 ID는 겹치지 않지만 방어적으로 ID 기준 중복 제거한다
- suptReginCdList=LC00,LC01,LC08 (전국·서울·경기)로 지역 필터를 서버에서 적용한다
- 마지막 페이지를 넘기면 '등록된 데이터가 없습니다.' 행 하나만 나온다
- 행의 div.top span.flag.agency는 기관 유형(공공/민간/지자체/교육)이고 주관기관이 아니다.
  주관기관은 div.bottom span.list의 두 번째 항목이다

상세: bizpbanc-ongoing.do?schM=view&pbancSn={id} (서버사이드 렌더링)
- 마감된 공고는 빈 껍데기 페이지가 오므로 bizpbanc-deadline.do로 다시 조회한다
"""
import asyncio
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from ..utils.dates import calculate_d_day, today_kst
from .base_crawler import BaseCrawler

TAB_NAMES = {
    "PBC010": "공공기관",
    "PBC020": "민간기관",
}

# div.bottom span.list 중 '라벨 값' 형식인 항목
LIST_META_PATTERN = re.compile(r"^(등록일자|시작일자|마감일자|조회)\s*(.*)$")
PAGE_LINK_PATTERN = re.compile(r"fn_egov_link_page\((\d+)\)")
VIEW_ID_PATTERN = re.compile(r"go_view(?:_blank)?\((\d+)\)")

# 공고문이 아닌 첨부파일 (Bedrock은 첫 번째 지원 형식 파일만 읽으므로 뒤로 보낸다)
NON_NOTICE_KEYWORDS = ("양식", "서식", "신청서", "동의서", "서약서", "확인서", "포스터")


def _clean(text: Optional[str]) -> str:
    """연속 공백 정리"""
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_date(text: str) -> Optional[datetime]:
    """'YYYY-MM-DD' 또는 'YYYY.MM.DD' 파싱"""
    match = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", text or "")
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups()))
    except ValueError:
        return None


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


class KStartupCrawler(BaseCrawler):
    """K-Startup 크롤러 (서버사이드 렌더링, 페이지네이션)"""

    source = Source.KSTARTUP
    name = "K-Startup"

    def __init__(
        self,
        max_pages: Optional[int] = None,
        page_delay: Optional[float] = None,
        tabs: Optional[list[str]] = None,
        region_codes: Optional[list[str]] = None,
    ):
        self.config = CRAWL_SOURCES["kstartup"]
        self.max_pages = max_pages if max_pages is not None else self.config["max_pages"]
        self.page_delay = page_delay if page_delay is not None else self.config["page_delay"]
        self.tabs = tabs if tabs is not None else list(self.config["tabs"])
        self.region_codes = (
            region_codes if region_codes is not None else list(self.config["region_codes"])
        )
        self._last_request_at: Optional[float] = None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
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

    def _list_params(self, tab: str, page: int) -> dict:
        params = {"page": page, "pbancClssCd": tab}
        if self.region_codes:
            params["suptReginCdList"] = ",".join(self.region_codes)
        return params

    async def crawl(self, max_items: int = 300) -> list[Announcement]:
        """공고 목록 크롤링

        두 탭을 페이지 단위로 번갈아 요청한다 (max_items가 작아도 한 탭만 채우지 않도록).
        탭별로 빈 페이지가 나오거나, 마지막 페이지에 도달하거나, max_pages에 도달하면 멈춘다.
        """
        announcements: list[Announcement] = []
        seen_ids: set[str] = set()
        next_page = {tab: 1 for tab in self.tabs}

        while next_page and len(announcements) < max_items:
            for tab in list(next_page):
                if len(announcements) >= max_items:
                    break

                page = next_page[tab]
                tab_name = TAB_NAMES.get(tab, tab)
                # main.py는 모든 크롤러를 하나의 try로 감싸므로 예외를 밖으로 던지지 않는다
                try:
                    html = await self._get(self.config["list_url"], self._list_params(tab, page))
                    items, last_page = self.parse_list_page(html)
                except requests.RequestException as e:
                    status = e.response.status_code if e.response is not None else None
                    logger.warning(
                        f"K-Startup {tab_name} 페이지 {page} 요청 실패 (HTTP {status}): {e}"
                    )
                    del next_page[tab]
                    continue
                except Exception as e:
                    logger.warning(f"K-Startup {tab_name} 페이지 {page} 처리 오류: {e}")
                    del next_page[tab]
                    continue

                if not items:
                    if page == 1:
                        logger.warning(f"K-Startup {tab_name} 탭 0건 - 사이트 구조 변경 여부 확인 필요")
                    del next_page[tab]
                    continue

                added = 0
                for ann in items:
                    if ann.id in seen_ids:
                        continue
                    seen_ids.add(ann.id)
                    announcements.append(ann)
                    added += 1

                logger.info(
                    f"K-Startup {tab_name} 페이지 {page}/{last_page or '?'}: {len(items)}건 "
                    f"(신규 {added}건, 누적 {len(announcements)}건)"
                )

                if last_page is not None and page >= last_page:
                    del next_page[tab]
                elif page >= self.max_pages:
                    logger.warning(
                        f"K-Startup {tab_name} 최대 페이지({self.max_pages}) 도달 - "
                        f"마지막 페이지 {last_page or '?'}, 이후 공고 누락 가능"
                    )
                    del next_page[tab]
                else:
                    next_page[tab] = page + 1

        result = announcements[:max_items]
        if not result:
            logger.warning("K-Startup 크롤링 결과 0건 - 사이트 구조 변경 여부 확인 필요")
        else:
            logger.info(f"K-Startup 크롤링 완료: 총 {len(result)}건")
        return result

    def parse_list_page(self, html: str) -> tuple[list[Announcement], Optional[int]]:
        """목록 페이지 HTML 파싱

        Returns:
            (공고 목록, 마지막 페이지 번호)
        """
        soup = BeautifulSoup(html, "lxml")
        container = soup.select_one("div#bizPbancList")
        if container is None:
            logger.warning("K-Startup 목록 영역(div#bizPbancList)을 찾을 수 없음")
            return [], None

        announcements = []
        for li in container.select(":scope > ul > li"):
            try:
                ann = self._parse_item(li)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"K-Startup 아이템 파싱 오류: {e}")

        return announcements, self._parse_last_page(soup)

    @staticmethod
    def _parse_last_page(soup: BeautifulSoup) -> Optional[int]:
        """div.paginate의 페이지 번호 중 최댓값 (10페이지 이하이면 a.last 버튼이 없다)"""
        paginate = soup.select_one("div.paginate")
        if paginate is None:
            return None
        numbers = [int(n) for n in PAGE_LINK_PATTERN.findall(str(paginate))]
        active = paginate.select_one("a.active")
        if active and active.get_text(strip=True).isdigit():
            numbers.append(int(active.get_text(strip=True)))
        return max(numbers) if numbers else None

    def _parse_item(self, li) -> Optional[Announcement]:
        """목록 <li> 파싱

        div.top: span.flag.agency(기관 유형), span.flag.typeNN(지원분야), span.flag.day(D-n)
        div.middle: a[href='javascript:go_view(pbancSn);'] > p.tit(제목)
        div.bottom span.list x6: 사업명, 주관기관, 등록일자, 시작일자, 마감일자, 조회
        """
        link = li.select_one("div.middle a[href*='go_view']")
        match = VIEW_ID_PATTERN.search(link.get("href", "")) if link else None
        if not match:
            # '등록된 데이터가 없습니다.' 행 등
            return None
        ann_id = match.group(1)

        title_input = li.select_one("input[name='scrap_list_bizPbancNm']")
        title = _clean(title_input.get("value")) if title_input else ""
        if not title:
            title_el = li.select_one("div.middle p.tit")
            title = _clean(title_el.get_text(" ", strip=True)) if title_el else ""
        if not title:
            return None

        category = None
        day_label = None
        for flag in li.select("div.top span.flag"):
            classes = flag.get("class") or []
            text = _clean(flag.get_text(" ", strip=True))
            if "agency" in classes:
                continue  # 공공/민간/지자체/교육 - 기관 유형이지 주관기관이 아님
            if "day" in classes:
                day_label = text
            elif category is None and text:
                category = text

        meta: dict[str, str] = {}
        plain: list[str] = []
        for span in li.select("div.bottom span.list"):
            text = _clean(span.get_text(" ", strip=True))
            meta_match = LIST_META_PATTERN.match(text)
            if meta_match:
                meta[meta_match.group(1)] = meta_match.group(2).strip()
            elif text:
                plain.append(text)
        organization = plain[1] if len(plain) > 1 else None

        deadline = _parse_date(meta.get("마감일자", ""))
        if deadline:
            deadline = deadline.replace(hour=23, minute=59, second=59)
        posted_date = _parse_date(meta.get("등록일자", ""))

        if deadline:
            d_day = calculate_d_day(deadline, today_kst())
        else:
            d_day = self._parse_d_day_label(day_label)

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=d_day,
            organization=organization,
            url=self.config["detail_url_template"].format(id=ann_id),
            posted_date=posted_date,
        )

    @staticmethod
    def _parse_d_day_label(label: Optional[str]) -> Optional[int]:
        """'D-13' → 13, 'D-day'/'오늘마감' → 0 (마감일자가 없을 때만 사용)"""
        if not label:
            return None
        match = re.search(r"D-(\d+)", label)
        if match:
            return int(match.group(1))
        if re.search(r"(?i)d-?day|오늘", label):
            return 0
        return None

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회 (KV 요약 + 본문 + 첨부파일)

        모집중 경로에 본문이 없으면(마감된 공고) 마감 공고 경로로 한 번 더 조회한다.
        빈 페이지에 있는 div#result_url[data-url](사이트 자체 JS 리다이렉트 대상)을 우선 사용한다.
        """
        ongoing_url = self.config["detail_url_template"].format(id=announcement_id)
        closed_url = self.config["closed_detail_url_template"].format(id=announcement_id)

        html = await self._fetch_detail_html(ongoing_url, announcement_id)
        detail = self.parse_detail_page(html) if html else None
        if detail:
            return detail

        next_url = (self._redirect_target(html) if html else None) or closed_url
        if next_url == ongoing_url:
            next_url = closed_url
        logger.info(f"K-Startup 상세 본문 없음 ({announcement_id}) - 마감 공고 경로로 재시도")
        html = await self._fetch_detail_html(next_url, announcement_id)
        detail = self.parse_detail_page(html) if html else None
        if detail:
            return detail

        logger.warning(f"K-Startup 상세 내용을 찾을 수 없음 ({announcement_id})")
        return None

    async def _fetch_detail_html(self, url: str, announcement_id: str) -> Optional[str]:
        try:
            return await self._get(url)
        except requests.RequestException as e:
            status = e.response.status_code if e.response is not None else None
            logger.warning(f"K-Startup 상세 조회 실패 ({announcement_id}, HTTP {status}): {url} - {e}")
            return None

    def _redirect_target(self, html: str) -> Optional[str]:
        """빈 상세 페이지의 리다이렉트 대상 URL (같은 호스트일 때만 사용)"""
        target = BeautifulSoup(html, "lxml").select_one("div#result_url[data-url]")
        if target is None:
            return None
        url = urljoin(self.config["base_url"], target["data-url"].strip())
        if urlparse(url).netloc != urlparse(self.config["base_url"]).netloc:
            return None
        return url

    def parse_detail_page(self, html: str) -> Optional[dict]:
        """상세 페이지 HTML 파싱

        Returns:
            {'content', 'attachments', 'fields'} 또는 None (본문 없는 빈 페이지)
        """
        soup = BeautifulSoup(html, "lxml")
        if soup.select_one("div.table_inner") is None:
            return None

        lines: list[str] = []
        title_el = soup.select_one("div.app_notice_details-wrap div.title h3")
        if title_el:
            lines.append(_clean(title_el.get_text(" ", strip=True)))

        # 상단 요약 박스: 지원분야, 기관구분, 담당부서, 지역, 접수기간, 주관기관명, 대상, 창업업력 ...
        fields: dict[str, str] = {}
        summary_box = soup.select_one("div.bg_box")
        if summary_box is not None:
            inners = summary_box.select("div.table_inner")
        else:
            inners = [
                el for el in soup.select("div.table_inner")
                if el.find_parent("div", class_="information_list-wrap") is None
            ]
        for inner in inners:
            label, value = inner.select_one(".tit"), inner.select_one(".txt")
            if label and value:
                key = _clean(label.get_text(" ", strip=True))
                val = _clean(value.get_text(" ", strip=True))
                if key and val:
                    fields.setdefault(key, val)
        lines.extend(f"{key}: {val}" for key, val in fields.items())

        intro = soup.select_one("div.information_box-wrap div.box")
        if intro:
            lines.append(_clean(intro.get_text(" ", strip=True)))

        # 신청방법 및 대상 / 제출서류 / 선정절차 / 지원내용 / 문의처
        for section in soup.select("div.information_list-wrap div.information_list"):
            heading = section.select_one("p.title")
            if heading:
                lines.append(f"[{_clean(heading.get_text(' ', strip=True))}]")
            for item in section.select("li.dot_list"):
                label = item.select_one("div.table_inner .tit")
                value = item.select_one("div.table_inner .txt")
                if not (label and value):
                    # 제출서류/지원내용 등: p.tit(항목명) + div.list_wrap(설명)
                    label = item.find("p", class_="tit", recursive=False)
                    value = item.find("div", class_="list_wrap", recursive=False)
                if label and value:
                    text = f"{_clean(label.get_text(' ', strip=True))}: {_clean(value.get_text(' ', strip=True))}"
                else:
                    text = _clean(item.get_text(" ", strip=True))
                if text:
                    lines.append(f"- {text}")

        content = "\n".join(line for line in lines if line)
        return {
            "content": content[:5000],
            "attachments": self._parse_attachments(soup),
            "fields": fields,
        }

    def _parse_attachments(self, soup: BeautifulSoup) -> list[dict]:
        """첨부파일: li.clear > a.file_bg(파일명) + a.btn_down[href='/afile/fileDownload/...']"""
        rows = soup.select("div.board_file li.clear") or soup.select("li.clear")
        attachments = []
        seen_urls = set()
        for row in rows:
            name_el = row.select_one("a.file_bg")
            down = row.select_one("a.btn_down[href]")
            if not name_el or not down:
                continue
            href = down["href"].strip()
            if not href or href.lower().startswith("javascript"):
                continue
            name = _clean(name_el.get_text(" ", strip=True))
            if not name:
                name = re.sub(r"^\[첨부파일\]\s*", "", _clean(name_el.get("title")))
            url = urljoin(self.config["base_url"], href)
            if not name or url in seen_urls:
                continue
            seen_urls.add(url)
            attachments.append({"name": name, "url": url})
        return sort_attachments(attachments)

    async def close(self):
        """세션 종료"""
        self.session.close()
