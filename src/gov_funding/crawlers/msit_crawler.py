"""과학기술정보통신부(MSIT) 크롤러

- 목록: JS로 데이터가 채워지므로 Playwright 사용.
  목록에서 td[id^="td_NTT_SJ_"] 등의 ID 패턴으로 데이터 추출.
- 상세: 정적 HTML이므로 requests + BeautifulSoup으로 조회한다 (실패 시 Playwright로 재시도).
  상세 URL에 bbsSeqNo=100이 없으면 HTTP 200으로 '시스템 점검 안내' 오류 페이지가 온다.
- 첨부파일: a.down[onclick="fn_download('55355', '1', 'hwpx')"] → /ssm/file/fileDown.do (일반 GET)
- 마감일: 목록·상세 HTML에는 없고 첨부 공고문(HWPX)에만 있다 → utils.deadline_text 참고
"""
import asyncio
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler

try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not available - MSIT crawler disabled")

# chromium 실행 옵션. '--single-process'는 절대 넣지 말 것:
# 단일 프로세스 모드는 브라우저 수명 동안 페이지(컨텍스트)를 하나만 지원해서
# 두 번째 new_page()에서 chromium이 크래시한다 (→ 상세 수집 전부 실패).
BROWSER_ARGS = ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]

_FN_DOWNLOAD = re.compile(r"fn_download\(\s*'?(\d+)'?\s*,\s*'?(\d+)'?")
_BLOCK_TAGS = ["p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"]


def _element_text(el) -> str:
    """요소 텍스트 추출 (블록 요소 단위 줄바꿈, 주석·스크립트 제외)

    MSIT 본문은 한 문단이 여러 <span>으로 쪼개져 있어 get_text("\\n")을 쓰면 줄이 깨진다.
    """
    for tag in el.select("script, style"):
        tag.decompose()
    for br in el.find_all("br"):
        br.replace_with("\n")
    for tag in el.find_all(["td", "th"]):
        tag.append(" ")
    for tag in el.find_all(_BLOCK_TAGS):
        tag.append("\n")
    lines = (re.sub(r"[ \t\u00a0\u3000]+", " ", line).strip() for line in el.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def _is_error_soup(soup: BeautifulSoup) -> bool:
    title = soup.title.get_text(strip=True) if soup.title else ""
    return "시스템 점검" in title


def is_msit_error_page(html: str) -> bool:
    """MSIT 오류 페이지('시스템 점검 안내') 여부

    잘못된 파라미터(bbsSeqNo 누락 등)에도 HTTP 200으로 이 페이지를 돌려준다.
    """
    return _is_error_soup(BeautifulSoup(html, "lxml"))


def parse_msit_attachments(soup: BeautifulSoup, attachment_url_template: str) -> list[dict]:
    """첨부파일 목록 추출

    <ul class="down_file"><li>
      <a class="ico_file_hwp">공고문.hwpx</a>
      <div class="down_btn"><a class="down" onclick="fn_download('55355', '1', 'hwpx');">다운로드</a></div>
    </li></ul>
    """
    attachments = []
    for li in soup.select("ul.down_file > li"):
        name_el = li.select_one("a[class^='ico_file']")
        down_el = li.select_one("a[onclick*='fn_download']")
        if not name_el or not down_el:
            continue
        match = _FN_DOWNLOAD.search(down_el.get("onclick", ""))
        name = name_el.get_text(strip=True)
        if not match or not name:
            continue
        url = attachment_url_template.format(atch_file_no=match.group(1), file_ord=match.group(2))
        attachments.append({"name": name, "url": url})
    return attachments


def parse_msit_detail(html: str, attachment_url_template: Optional[str] = None) -> Optional[dict]:
    """상세 페이지 HTML 파싱

    Returns:
        {"content": 본문 텍스트, "attachments": [{"name", "url"}]}.
        오류 페이지이거나 게시물 영역(.board_view)이 없으면 None.
    """
    soup = BeautifulSoup(html, "lxml")
    if _is_error_soup(soup):
        return None

    board = soup.select_one(".board_view")
    if board is None:
        return None

    template = attachment_url_template or CRAWL_SOURCES["msit"]["attachment_url_template"]
    attachments = parse_msit_attachments(board, template)

    # 첨부파일 목록·이전/다음글이 섞이지 않도록 본문(.view_cont)만 사용
    content_el = board.select_one(".view_cont") or board
    content = _element_text(content_el)

    return {"content": content[:5000], "attachments": attachments}


class MsitCrawler(BaseCrawler):
    """과학기술정보통신부 크롤러 (목록: JS 렌더링, 상세: 정적 HTML)"""

    source = Source.MSIT
    name = "MSIT"

    def __init__(self, page_delay: float = 3.0, request_timeout: int = 30):
        self.config = CRAWL_SOURCES["msit"]
        self.browser: Optional[Browser] = None
        self.playwright = None
        self.page_delay = page_delay
        self.request_timeout = request_timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })

    async def _init_browser(self):
        """브라우저 초기화"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        # 크롬이 죽으면 browser 객체는 남아있지만 연결이 끊긴다.
        # is_connected()까지 확인해 죽은 객체를 재사용하지 않는다.
        if self.browser is not None and not self.browser.is_connected():
            logger.warning("MSIT Playwright 브라우저 연결 끊김 - 재시작")
            self.browser = None

        if self.browser is None:
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=list(BROWSER_ARGS),
            )
            logger.info("MSIT Playwright 브라우저 시작됨")

    async def _discard_browser(self):
        """크래시한 브라우저 정리 (playwright 드라이버는 유지)"""
        browser, self.browser = self.browser, None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    def _is_browser_crash(self, error: Exception) -> bool:
        """TargetClosedError 등 브라우저가 죽어서 난 오류인지"""
        if type(error).__name__ == "TargetClosedError" or "has been closed" in str(error):
            return True
        return self.browser is not None and not self.browser.is_connected()

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 목록 크롤링

        페이지 파싱/이동 중 오류가 나면 WARNING을 남기고 그때까지 수집한 공고를 반환한다
        (예전에는 2페이지에서 'page is navigating' 오류가 나면 1페이지 결과까지 버리고 0건을 반환했다).
        """
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright not available for MSIT")
            return []

        announcements: list[Announcement] = []
        seen_ids: set[str] = set()
        max_pages = 5
        page = None
        try:
            await self._init_browser()
            page = await self.browser.new_page()
            await page.goto(self.config["list_url"], wait_until="networkidle", timeout=60000)
            await asyncio.sleep(self.page_delay)

            for page_num in range(1, max_pages + 1):
                # 페이지별로 오류를 잡아 앞 페이지까지의 결과를 지킨다
                try:
                    added = 0
                    for ann in await self._parse_page(page):
                        if ann.id not in seen_ids:
                            seen_ids.add(ann.id)
                            announcements.append(ann)
                            added += 1

                    logger.info(f"MSIT 페이지 {page_num}: {added}건 추가 (총 {len(announcements)}건)")

                    if not added:
                        break

                    # 이미 충분히 모았으면 다음 페이지를 불러오지 않는다
                    if len(announcements) >= max_items or page_num >= max_pages:
                        break

                    if not await self._go_next_page(page, page_num):
                        break

                    await asyncio.sleep(self.page_delay)
                except Exception as e:
                    logger.warning(
                        f"MSIT 페이지 {page_num} 처리 오류 - 수집 중단 (지금까지 {len(announcements)}건 유지): {e}"
                    )
                    break
        except Exception as e:
            # 브라우저 시작/목록 첫 페이지 열기 실패
            logger.error(f"MSIT 크롤링 오류 (목록 페이지 열기 실패): {e}")
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

        result = announcements[:max_items]
        if not result:
            logger.warning("MSIT 크롤링 결과 0건 - 목록 페이지 구조 변경 여부 확인 필요")
        else:
            logger.info(f"MSIT 크롤링 완료: 총 {len(result)}건")
        return result

    async def _parse_page(self, page: Page) -> list[Announcement]:
        """현재 페이지의 공고 파싱

        MSIT div 기반 구조:
        <div class="board_list">
          <div class="toggle thead">...</div>  <!-- 헤더, 스킵 -->
          <div class="toggle">                  <!-- 각 공고 아이템 -->
            <a onclick="fn_detail(nttSeqNo);">
              <div class="num" id="td_NO_{i}">번호</div>
              <div class="txt">
                <p class="title" id="td_NTT_SJ_{i}">제목</p>
                <div class="meta">
                  <dl><dd id="td_CHRG_DEPT_NM_{i}">부서</dd></dl>
                </div>
              </div>
              <div class="file" id="td_FILE_{i}">...</div>
              <div class="date" id="td_REG_DT_{i}">Feb 20, 2026</div>
            </a>
          </div>
        </div>
        """
        announcements = []

        # thead 제외한 toggle 아이템 선택
        items = await page.query_selector_all("div.board_list > div.toggle:not(.thead)")

        for item in items:
            try:
                ann = await self._parse_item(item)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"MSIT 아이템 파싱 오류: {e}")

        return announcements

    async def _parse_item(self, item) -> Optional[Announcement]:
        """개별 공고 아이템 파싱"""
        # 제목 추출 (p.title)
        title_elem = await item.query_selector("p.title")
        if not title_elem:
            return None

        title = (await title_elem.inner_text()).strip()
        if not title:
            return None

        # ID 추출: 부모 <a> 태그의 onclick="fn_detail(nttSeqNo)"
        ann_id = None
        link = await item.query_selector("a[onclick*='fn_detail']")
        if link:
            onclick = await link.get_attribute("onclick") or ""
            id_match = re.search(r"fn_detail\s*\(\s*'?(\d+)'?\s*\)", onclick)
            if id_match:
                ann_id = id_match.group(1)

        if not ann_id:
            # 제목 해시로 대체하지 않는다 — 마크업이 바뀌면 저장 키 체계가 바뀌어 같은 공고가 다시 신규로 알림된다
            logger.warning(f"MSIT 게시물 번호(fn_detail)를 찾을 수 없어 건너뜀: {title}")
            return None

        # 등록일 (div.date, 형식: "Feb 20, 2026")
        posted_date = None
        date_elem = await item.query_selector("div.date")
        if date_elem:
            date_text = (await date_elem.inner_text()).strip()
            for fmt in ("%b %d, %Y", "%Y-%m-%d", "%Y.%m.%d"):
                try:
                    posted_date = datetime.strptime(date_text, fmt)
                    break
                except ValueError:
                    continue

        # 담당부서 (dd[id^='td_CHRG_DEPT_NM_'])
        dept = None
        dept_elem = await item.query_selector("dd[id^='td_CHRG_DEPT_NM_']")
        if dept_elem:
            dept = (await dept_elem.inner_text()).strip()

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=None,
            # 마감일은 첨부 공고문에만 있다 (utils.deadline_text.parse_deadline_from_text)
            deadline=None,
            d_day=None,
            department="과학기술정보통신부",
            organization=dept or "MSIT",
            url=self.detail_url(ann_id),
            posted_date=posted_date,
        )

    async def _go_next_page(self, page: Page, current_page: int) -> bool:
        """다음 페이지로 이동

        fn_paging(pageNo)는 form GET submit으로 전체 페이지 리로드됨.
        """
        try:
            next_page = current_page + 1

            # fn_paging(pageNo) JS 호출 → form submit으로 네비게이션 발생
            try:
                async with page.expect_navigation(timeout=15000):
                    await page.evaluate(f"fn_paging({next_page})")
                await asyncio.sleep(2)
                return True
            except Exception:
                pass

            # '다음' 링크 클릭 시도
            next_link = await page.query_selector("a:has-text('다음')")
            if next_link:
                onclick = await next_link.get_attribute("onclick") or ""
                if "fn_paging" in onclick:
                    await next_link.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    await asyncio.sleep(2)
                    return True

            return False
        except Exception as e:
            logger.debug(f"MSIT 다음 페이지 이동 실패: {e}")
            return False

    def detail_url(self, announcement_id: str) -> str:
        """상세 페이지 URL (Slack 링크·상세 조회 공용)"""
        return self.config["detail_url_template"].format(id=announcement_id)

    async def _fetch_html(self, url: str) -> Optional[str]:
        """상세 페이지 HTML 조회 — requests 우선, 실패 시 Playwright로 재시도"""
        try:
            resp = await asyncio.to_thread(self.session.get, url, timeout=self.request_timeout)
            resp.raise_for_status()
            return resp.text
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in (404, 410):
                logger.warning(f"MSIT 상세 페이지 없음 (HTTP {status}): {url}")
                return None
            logger.warning(f"MSIT 상세 페이지 요청 실패 - 브라우저로 재시도 ({url}): {e}")
        except Exception as e:
            logger.warning(f"MSIT 상세 페이지 요청 실패 - 브라우저로 재시도 ({url}): {e}")

        return await self._fetch_html_with_browser(url)

    async def _fetch_html_with_browser(self, url: str) -> Optional[str]:
        """Playwright로 HTML 조회 (브라우저가 죽어 있으면 재시작 후 1회 재시도)"""
        if not PLAYWRIGHT_AVAILABLE:
            return None

        for attempt in (1, 2):
            page = None
            try:
                await self._init_browser()
                page = await self.browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                return await page.content()
            except Exception as e:
                if attempt == 1 and self._is_browser_crash(e):
                    logger.warning(f"MSIT 브라우저 연결 끊김 - 재시작 후 재시도 ({url}): {e}")
                    await self._discard_browser()
                    continue
                logger.warning(f"MSIT 브라우저 상세 조회 실패 ({url}): {e}")
                return None
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass
        return None

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회

        Returns:
            {"content": 본문, "attachments": [{"name", "url"}]} 또는 None (실패 시 WARNING)
        """
        url = self.detail_url(announcement_id)
        try:
            html = await self._fetch_html(url)
            if html is None:
                logger.warning(f"MSIT 상세 조회 실패 ({announcement_id}): {url}")
                return None

            if is_msit_error_page(html):
                logger.warning(
                    f"MSIT 상세 페이지가 '시스템 점검 안내' 오류 페이지로 응답 "
                    f"({announcement_id}) - URL 파라미터(bbsSeqNo) 확인 필요: {url}"
                )
                return None

            detail = parse_msit_detail(html, self.config["attachment_url_template"])
            if detail is None:
                logger.warning(f"MSIT 상세 페이지에 게시물 영역(.board_view) 없음 ({announcement_id}): {url}")
                return None

            if not detail["content"] and not detail["attachments"]:
                logger.warning(f"MSIT 상세 본문·첨부파일 모두 비어 있음 ({announcement_id}): {url}")
            return detail
        except Exception as e:
            logger.warning(f"MSIT 상세 조회 오류 ({announcement_id}): {e}")
            return None

    async def close(self):
        """리소스 정리"""
        self.session.close()
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug(f"MSIT 브라우저 종료 오류: {e}")
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        logger.info("MSIT Playwright 브라우저 종료됨")
