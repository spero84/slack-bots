"""IITP(정보통신기획평가원) 크롤러 - Playwright 기반

Vue.js 클라이언트 렌더링 사이트이므로 Playwright 필요.
"""
import asyncio
import hashlib
import re
from datetime import datetime
from typing import Optional

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler

try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not available - IITP crawler disabled")


class IitpCrawler(BaseCrawler):
    """IITP 크롤러 (Vue.js 클라이언트 렌더링)"""

    source = Source.IITP
    name = "IITP"

    def __init__(self):
        self.config = CRAWL_SOURCES["iitp"]
        self.browser: Optional[Browser] = None
        self.playwright = None

    async def _init_browser(self):
        """브라우저 초기화"""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed")

        if self.browser is None:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--single-process",
                ],
            )
            logger.info("IITP Playwright 브라우저 시작됨")

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 목록 크롤링"""
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright not available for IITP")
            return []

        await self._init_browser()
        page = await self.browser.new_page()

        try:
            # 페이지 로드 및 Vue.js 렌더링 대기
            await page.goto(self.config["list_url"], wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3)  # Vue.js 데이터 바인딩 대기

            # 30개씩 표시하도록 변경 (옵션 있을 경우)
            try:
                size_select = await page.query_selector("select[v-model*='pageSize'], select.page_size")
                if size_select:
                    await size_select.select_option(value="30")
                    await asyncio.sleep(2)
            except Exception:
                pass

            announcements = []
            page_num = 1
            max_pages = 5

            while len(announcements) < max_items and page_num <= max_pages:
                items = await self._parse_page(page)
                new_items = [i for i in items if i.id not in {a.id for a in announcements}]
                announcements.extend(new_items)

                logger.info(f"IITP 페이지 {page_num}: {len(new_items)}건 추가 (총 {len(announcements)}건)")

                if not new_items:
                    break

                # 다음 페이지로 이동
                next_clicked = await self._click_next_page(page, page_num)
                if not next_clicked:
                    break

                await asyncio.sleep(2)  # 페이지 로드 대기
                page_num += 1

            result = announcements[:max_items]
            logger.info(f"IITP 크롤링 완료: 총 {len(result)}건")
            return result

        except Exception as e:
            logger.error(f"IITP 크롤링 오류: {e}")
            return []
        finally:
            await page.close()

    async def _click_next_page(self, page: Page, current_page: int) -> bool:
        """다음 페이지 클릭"""
        try:
            # 다음 페이지 번호 버튼 또는 '다음' 버튼 클릭
            next_page = current_page + 1

            # 페이지 번호 버튼 시도
            page_btn = await page.query_selector(f"a:has-text('{next_page}'):not(:has-text('다'))")
            if page_btn:
                await page_btn.click()
                return True

            # '다음' 또는 '>' 버튼 시도
            for selector in ["a:has-text('다음')", "a:has-text('>')", ".pagination .next a", "a.next"]:
                next_btn = await page.query_selector(selector)
                if next_btn:
                    is_disabled = await next_btn.get_attribute("disabled")
                    classes = await next_btn.get_attribute("class") or ""
                    if not is_disabled and "disabled" not in classes:
                        await next_btn.click()
                        return True

            return False
        except Exception as e:
            logger.debug(f"IITP 다음 페이지 클릭 실패: {e}")
            return False

    async def _parse_page(self, page: Page) -> list[Announcement]:
        """현재 페이지의 공고 파싱"""
        announcements = []

        # Vue.js 렌더링된 목록 아이템 선택
        # 테이블 또는 리스트 형태일 수 있음
        items = await page.query_selector_all("table tbody tr")
        if not items:
            items = await page.query_selector_all("ul.list_wrap li, .board_list li, .list_area li")

        for item in items:
            try:
                ann = await self._parse_item(item)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"IITP 아이템 파싱 오류: {e}")

        return announcements

    async def _parse_item(self, item) -> Optional[Announcement]:
        """개별 공고 아이템 파싱

        IITP Vue.js 템플릿 구조:
        - 순번: {{totalcount - item.rn + 1}}
        - 제목: {{ item.title }}
        - 접수기간: {{item.receipt_begin_date}} ~ {{item.receipt_end_date}}
        - 담당자: {{item.charger_name}}
        """
        text = await item.inner_text()
        if not text or "등록된 게시물이 없습니다" in text:
            return None

        inner_html = await item.inner_html()

        # 제목 추출
        title = ""
        title_elem = await item.query_selector("a, .title, td:nth-child(2) a, td:nth-child(2)")
        if title_elem:
            title = (await title_elem.inner_text()).strip()

        if not title:
            return None

        # 빈 행이나 헤더 행 스킵
        if title in ["제목", "순번", "접수기간", "담당자"]:
            return None

        # ID 추출: onclick, href, data 속성에서
        ann_id = None

        # onclick에서 seq/id 추출
        id_match = re.search(
            r"(?:seq|idx|sn|id)[=:'\s]+(\d+)|view\s*\(\s*'?(\d+)|fn_detail\s*\(\s*'?(\d+)|goView\s*\(\s*'?(\d+)",
            inner_html,
            re.IGNORECASE,
        )
        if id_match:
            ann_id = next(g for g in id_match.groups() if g)

        # href에서 시도
        if not ann_id:
            href_match = re.search(r'href="[^"]*[?&]seq=(\d+)', inner_html)
            if href_match:
                ann_id = href_match.group(1)

        # data 속성에서 시도
        if not ann_id:
            data_match = re.search(r'data-(?:seq|id|idx)="(\d+)"', inner_html)
            if data_match:
                ann_id = data_match.group(1)

        if not ann_id:
            ann_id = hashlib.md5(title.encode()).hexdigest()[:12]

        # 접수기간에서 마감일 추출
        deadline = None
        d_day = None
        period_match = re.search(
            r'(\d{4}[.\-/]\d{2}[.\-/]\d{2})\s*~\s*(\d{4}[.\-/]\d{2}[.\-/]\d{2})',
            text,
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

        # 담당자 추출 (마지막 컬럼)
        cells = await item.query_selector_all("td")
        charger_name = None
        if cells and len(cells) >= 4:
            charger_elem = cells[-1]
            charger_name = (await charger_elem.inner_text()).strip()

        # 상세 URL
        url = self.config["detail_url_template"].format(id=ann_id)

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=None,
            deadline=deadline,
            d_day=d_day,
            department="정보통신기획평가원",
            organization=charger_name or "IITP",
            url=url,
        )

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회"""
        if not PLAYWRIGHT_AVAILABLE:
            return None

        await self._init_browser()
        page = await self.browser.new_page()

        try:
            url = self.config["detail_url_template"].format(id=announcement_id)
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            # 상세 내용 추출
            content_elem = await page.query_selector(
                ".view_cont, .content, .board_view, .detail_wrap"
            )
            content = await content_elem.inner_text() if content_elem else ""

            # 첨부파일 목록
            attachments = []
            file_links = await page.query_selector_all(
                "a[href*='download'], a.file_down, .file_list a"
            )
            for link in file_links:
                name = await link.inner_text()
                href = await link.get_attribute("href")
                if name and href:
                    attachments.append({
                        "name": name.strip(),
                        "url": href,
                    })

            return {
                "content": content[:5000],
                "attachments": attachments,
            }
        except Exception as e:
            logger.error(f"IITP 상세 조회 오류 ({announcement_id}): {e}")
            return None
        finally:
            await page.close()

    async def close(self):
        """리소스 정리"""
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        logger.info("IITP Playwright 브라우저 종료됨")
