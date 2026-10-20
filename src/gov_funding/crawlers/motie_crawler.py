"""산업통상자원부(MOTIE, 현 산업통상부 www.motir.go.kr) 크롤러

- 목록: Playwright로 이동(article.list 페이지네이션)하고, 행 파싱은 page.content()를
  BeautifulSoup으로 처리한다 (parse_motie_list). 열: 공고번호, 제목, 담당부서, 등록일, 조회수, 첨부파일
- 상세: https://www.motir.go.kr/kor/article/ATCL2826a2625/{id}/view — 정적 HTML이므로
  requests + BeautifulSoup으로 조회 (실패 시 Playwright로 재시도). '/view'가 없으면 404.
- 첨부파일: .board-detail .info-down a[href*="/attach/down/"] (일반 GET으로 다운로드 가능).
  링크 텍스트 끝의 ' [178.7 KB]' 크기 표기는 제거한다 (확장자 판별이 깨짐).
- 마감일: 목록·상세 HTML에는 없고 첨부 공고문에만 있다 → utils.deadline_text 참고
"""
import asyncio
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

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
    logger.warning("Playwright not available - MOTIE crawler disabled")

# chromium 실행 옵션. '--single-process'는 절대 넣지 말 것:
# 단일 프로세스 모드는 브라우저 수명 동안 페이지(컨텍스트)를 하나만 지원해서
# 두 번째 new_page()에서 chromium이 크래시한다 (→ 상세 수집 전부 실패).
BROWSER_ARGS = ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]

_VIEW_ID = re.compile(r"(?:article\.view|fn_view|goView|fn_detail)\s*\(\s*'?(\d+)'?\s*\)")
_HREF_ID = re.compile(r"/(\d+)/view(?:\?|$)")
_SIZE_SUFFIX = re.compile(r"\s*\[\s*[\d.,]+\s*(?:[KMGT]?B|bytes?)\s*\]\s*$", re.IGNORECASE)
# '등록된 게시물이 없습니다' 안내 행. '등록된'만으로 판단하면
# '혁신제품으로 등록된 중소기업 … 공고' 같은 실제 공고가 버려진다
_EMPTY_BOARD = re.compile(r"게시물이\s*없")
_BLOCK_TAGS = ["p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"]


def _element_text(el) -> str:
    """요소 텍스트 추출 (블록 요소 단위 줄바꿈, 주석·스크립트 제외)"""
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


def strip_size_suffix(name: str) -> str:
    """첨부파일명 끝의 크기 표기 제거: '공고.hwpx [178.7 KB]' → '공고.hwpx'"""
    return _SIZE_SUFFIX.sub("", name or "").strip()


def _parse_date(text: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_motie_row(row, detail_url_template: str) -> Optional[Announcement]:
    """목록 테이블 행(<tr>) 파싱"""
    cells = row.find_all("td", recursive=False)
    # 빈 게시판 안내 행은 보통 colspan 1칸이라 여기서 걸러진다
    if len(cells) < 4:
        return None

    row_text = row.get_text(" ", strip=True)
    if not row_text or _EMPTY_BOARD.search(row_text):
        return None

    title_link = cells[1].select_one("a")
    title = (title_link or cells[1]).get_text(" ", strip=True)
    if not title or title in ("제목", "공고명", "사업명"):
        return None

    # ID: 게시물 번호만 사용 — onclick="article.view('71365')" 또는 href '/71365/view'.
    # 공고번호(2026-643)나 제목 해시로 대체하면 상세 URL이 404가 되고 저장 키 체계가 바뀌어
    # 같은 공고가 다시 신규로 알림된다 (2026년 4~8월 실제 발생)
    ann_id = None
    if title_link is not None:
        id_match = _VIEW_ID.search(title_link.get("onclick", ""))
        if id_match:
            ann_id = id_match.group(1)
        if not ann_id:
            href_match = _HREF_ID.search(title_link.get("href", ""))
            if href_match:
                ann_id = href_match.group(1)
    if not ann_id:
        logger.warning(f"MOTIE 게시물 번호를 찾을 수 없어 건너뜀: {title}")
        return None

    dept = cells[2].get_text(strip=True) or None
    posted_date = _parse_date(cells[3].get_text(strip=True))

    return Announcement(
        id=ann_id,
        source=Source.MOTIE,
        title=title,
        category=None,
        # 마감일은 첨부 공고문에만 있다 (utils.deadline_text.parse_deadline_from_text)
        deadline=None,
        d_day=None,
        department="산업통상자원부",
        organization=dept or "MOTIE",
        # 목록 링크의 '?mno=&pageIndex=N'을 버리고 정규 URL 사용
        url=detail_url_template.format(id=ann_id),
        posted_date=posted_date,
    )


def parse_motie_list(html: str, detail_url_template: Optional[str] = None) -> list[Announcement]:
    """목록 페이지 HTML 파싱"""
    template = detail_url_template or CRAWL_SOURCES["motie"]["detail_url_template"]
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("table tbody tr") or soup.select(".board_list tbody tr, .list_table tbody tr")

    announcements = []
    for row in rows:
        try:
            ann = parse_motie_row(row, template)
            if ann:
                announcements.append(ann)
        except Exception as e:
            logger.debug(f"MOTIE 행 파싱 오류: {e}")
    return announcements


def parse_motie_detail(html: str, base_url: Optional[str] = None) -> Optional[dict]:
    """상세 페이지 HTML 파싱

    <div class="board-detail">
      <div class="detail-tit"><span>제목</span></div>
      <div class="detail-info"><ul> ... <li class="info-down">
        <a href="/attach/down/...">공고.hwpx [178.7 KB]</a>
        <a class="ico-magnifier" href="/attach/viewer/...">바로보기</a>
      </li></ul></div>
      <div class="detail-cont">본문</div>
    </div>

    Returns:
        {"content": 본문 텍스트, "attachments": [{"name", "url"}]}.
        게시물 영역(.board-detail)이 없으면(404 'ERROR' 페이지 등) None.
    """
    base = base_url or CRAWL_SOURCES["motie"]["base_url"]
    soup = BeautifulSoup(html, "lxml")
    board = soup.select_one(".board-detail")
    if board is None:
        return None

    attachments = []
    seen = set()
    # '/attach/viewer/' 바로보기 링크는 제외 (다운로드 링크만)
    for a in board.select(".info-down a[href*='/attach/down/']"):
        href = a.get("href", "")
        name = strip_size_suffix(a.get_text(" ", strip=True))
        if not name:
            name = re.sub(r"\s*다운로드\s*$", "", a.get("title", "")).strip()
        url = urljoin(base, href)
        if not name or not href or url in seen:
            continue
        seen.add(url)
        attachments.append({"name": name, "url": url})

    content_el = board.select_one(".detail-cont")
    content = _element_text(content_el) if content_el is not None else ""

    return {"content": content[:5000], "attachments": attachments}


class MotieCrawler(BaseCrawler):
    """산업통상자원부 크롤러 (목록: Playwright, 상세: 정적 HTML)"""

    source = Source.MOTIE
    name = "MOTIE"

    def __init__(self, page_delay: float = 3.0, request_timeout: int = 30):
        self.config = CRAWL_SOURCES["motie"]
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
            logger.warning("MOTIE Playwright 브라우저 연결 끊김 - 재시작")
            self.browser = None

        if self.browser is None:
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=list(BROWSER_ARGS),
            )
            logger.info("MOTIE Playwright 브라우저 시작됨")

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
            logger.error("Playwright not available for MOTIE")
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

            # 리다이렉트 후 실제 URL 확인
            logger.info(f"MOTIE 실제 URL: {page.url}")

            for page_num in range(1, max_pages + 1):
                # 페이지별로 오류를 잡아 앞 페이지까지의 결과를 지킨다
                try:
                    added = 0
                    for ann in await self._parse_page(page):
                        if ann.id not in seen_ids:
                            seen_ids.add(ann.id)
                            announcements.append(ann)
                            added += 1

                    logger.info(f"MOTIE 페이지 {page_num}: {added}건 추가 (총 {len(announcements)}건)")

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
                        f"MOTIE 페이지 {page_num} 처리 오류 - 수집 중단 (지금까지 {len(announcements)}건 유지): {e}"
                    )
                    break
        except Exception as e:
            # 브라우저 시작/목록 첫 페이지 열기 실패
            logger.error(f"MOTIE 크롤링 오류 (목록 페이지 열기 실패): {e}")
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

        result = announcements[:max_items]
        if not result:
            logger.warning("MOTIE 크롤링 결과 0건 - 목록 페이지 구조 변경 여부 확인 필요")
        else:
            logger.info(f"MOTIE 크롤링 완료: 총 {len(result)}건")
        return result

    async def _parse_page(self, page: Page) -> list[Announcement]:
        """현재 페이지의 공고 파싱 (정적 테이블이므로 HTML을 BeautifulSoup으로 파싱)"""
        html = await page.content()
        return parse_motie_list(html, self.config["detail_url_template"])

    async def _go_next_page(self, page: Page, current_page: int) -> bool:
        """다음 페이지로 이동

        article.list(N)은 searchForm에 pageIndex=N을 넣고 GET submit한다 (Article.js) → 전체 페이지 이동.
        evaluate 직후 wait_for_load_state()를 부르면 아직 이동 전인 이전 페이지 기준으로 바로 끝나서
        다음 page.content()가 'page is navigating' 오류를 냈다. 그래서 expect_navigation으로 이동 완료를 기다린다.
        """
        try:
            next_page = current_page + 1

            # article.list(pageIndex) JS 호출 → form GET submit으로 네비게이션 발생
            try:
                async with page.expect_navigation(wait_until="load", timeout=15000):
                    await page.evaluate(f"article.list({next_page})")
                return True
            except Exception as e:
                logger.debug(f"MOTIE article.list({next_page}) 이동 실패 - URL 이동으로 재시도: {e}")

            # ?pageIndex=N URL 파라미터 방식
            current_url = page.url
            if "pageIndex=" in current_url:
                next_url = re.sub(r'pageIndex=\d+', f'pageIndex={next_page}', current_url)
            else:
                separator = "&" if "?" in current_url else "?"
                next_url = f"{current_url}{separator}pageIndex={next_page}"

            try:
                await page.goto(next_url, wait_until="networkidle", timeout=30000)
                return True
            except Exception:
                pass

            # 페이지네이션 버튼 클릭
            for selector in ["a:has-text('다음')", "a:has-text('>')", ".pagination .next a"]:
                next_btn = await page.query_selector(selector)
                if next_btn:
                    is_disabled = await next_btn.get_attribute("disabled")
                    classes = await next_btn.get_attribute("class") or ""
                    if not is_disabled and "disabled" not in classes:
                        # 버튼도 article.list() → 페이지 이동이므로 고정 sleep 대신 이동 완료를 기다린다
                        async with page.expect_navigation(wait_until="load", timeout=15000):
                            await next_btn.click()
                        return True

            return False
        except Exception as e:
            logger.debug(f"MOTIE 다음 페이지 이동 실패: {e}")
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
                logger.warning(f"MOTIE 상세 페이지 없음 (HTTP {status}): {url}")
                return None
            logger.warning(f"MOTIE 상세 페이지 요청 실패 - 브라우저로 재시도 ({url}): {e}")
        except Exception as e:
            logger.warning(f"MOTIE 상세 페이지 요청 실패 - 브라우저로 재시도 ({url}): {e}")

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
                    logger.warning(f"MOTIE 브라우저 연결 끊김 - 재시작 후 재시도 ({url}): {e}")
                    await self._discard_browser()
                    continue
                logger.warning(f"MOTIE 브라우저 상세 조회 실패 ({url}): {e}")
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
                logger.warning(f"MOTIE 상세 조회 실패 ({announcement_id}): {url}")
                return None

            detail = parse_motie_detail(html, self.config["base_url"])
            if detail is None:
                logger.warning(
                    f"MOTIE 상세 페이지에 게시물 영역(.board-detail) 없음 ({announcement_id}) "
                    f"- 404/오류 페이지 가능성: {url}"
                )
                return None

            if not detail["content"] and not detail["attachments"]:
                logger.warning(f"MOTIE 상세 본문·첨부파일 모두 비어 있음 ({announcement_id}): {url}")
            return detail
        except Exception as e:
            logger.warning(f"MOTIE 상세 조회 오류 ({announcement_id}): {e}")
            return None

    async def close(self):
        """리소스 정리"""
        self.session.close()
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug(f"MOTIE 브라우저 종료 오류: {e}")
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        logger.info("MOTIE Playwright 브라우저 종료됨")
