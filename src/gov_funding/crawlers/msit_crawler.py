"""과학기술정보통신부(MSIT) 크롤러 - Playwright 기반

JS로 데이터가 채워지므로 Playwright 필수.
목록 테이블에서 td[id^="td_NTT_SJ_"] 등의 ID 패턴으로 데이터 추출.
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
    logger.warning("Playwright not available - MSIT crawler disabled")


class MsitCrawler(BaseCrawler):
    """과학기술정보통신부 크롤러 (JS 렌더링)"""

    source = Source.MSIT
    name = "MSIT"

    def __init__(self):
        self.config = CRAWL_SOURCES["msit"]
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
            logger.info("MSIT Playwright 브라우저 시작됨")

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 목록 크롤링"""
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright not available for MSIT")
            return []

        await self._init_browser()
        page = await self.browser.new_page()

        try:
            await page.goto(self.config["list_url"], wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3)

            announcements = []
            page_num = 1
            max_pages = 5

            while len(announcements) < max_items and page_num <= max_pages:
                items = await self._parse_page(page)
                new_items = [i for i in items if i.id not in {a.id for a in announcements}]
                announcements.extend(new_items)

                logger.info(f"MSIT 페이지 {page_num}: {len(new_items)}건 추가 (총 {len(announcements)}건)")

                if not new_items:
                    break

                # 다음 페이지로 이동
                next_clicked = await self._go_next_page(page, page_num)
                if not next_clicked:
                    break

                await asyncio.sleep(3)
                page_num += 1

            result = announcements[:max_items]
            logger.info(f"MSIT 크롤링 완료: 총 {len(result)}건")
            return result

        except Exception as e:
            logger.error(f"MSIT 크롤링 오류: {e}")
            return []
        finally:
            await page.close()

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
            ann_id = hashlib.md5(title.encode()).hexdigest()[:12]

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

        # 상세 URL
        url = self.config["detail_url_template"].format(id=ann_id)

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=None,
            deadline=None,
            d_day=None,
            department="과학기술정보통신부",
            organization=dept or "MSIT",
            url=url,
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
                ".view_cont, .content, .board_view, .detail_wrap, "
                "#divViewBody, .bbs_view_cont"
            )
            content = await content_elem.inner_text() if content_elem else ""

            # 첨부파일 목록
            attachments = []
            file_links = await page.query_selector_all(
                "a[href*='download'], a[href*='fileDown'], a.file_down, "
                ".file_list a, .attach a"
            )
            for link in file_links:
                name = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
                if name and href:
                    if not href.startswith("http"):
                        href = self.config["base_url"] + href
                    attachments.append({"name": name, "url": href})

            return {
                "content": content[:5000],
                "attachments": attachments,
            }
        except Exception as e:
            logger.error(f"MSIT 상세 조회 오류 ({announcement_id}): {e}")
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
        logger.info("MSIT Playwright 브라우저 종료됨")
