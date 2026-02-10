"""K-Startup 크롤러 - Playwright 기반"""
import asyncio
import re
from datetime import datetime
from typing import Optional

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from .base_crawler import BaseCrawler

# Playwright는 Lambda 환경에서 Layer로 제공됨
try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not available - K-Startup crawler disabled")


class KStartupCrawler(BaseCrawler):
    """K-Startup 크롤러 (JavaScript 동적 로딩)"""

    source = Source.KSTARTUP
    name = "K-Startup"

    def __init__(self):
        self.config = CRAWL_SOURCES["kstartup"]
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
            logger.info("Playwright 브라우저 시작됨")

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 목록 크롤링"""
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright not available")
            return []

        await self._init_browser()
        page = await self.browser.new_page()

        try:
            # 목록 페이지 접속
            await page.goto(self.config["list_url"], wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)  # 초기 로딩 대기

            announcements = []
            scroll_count = 0
            max_scrolls = 10

            while len(announcements) < max_items and scroll_count < max_scrolls:
                # 현재 페이지의 공고 파싱
                items = await self._parse_page(page)
                new_items = [item for item in items if item.id not in {a.id for a in announcements}]
                announcements.extend(new_items)

                logger.info(f"K-Startup 스크롤 {scroll_count + 1}: {len(new_items)}건 추가 (총 {len(announcements)}건)")

                if not new_items:
                    # 더 이상 새 항목이 없으면 종료
                    break

                # 스크롤 다운 (무한 스크롤)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)  # 로딩 대기

                scroll_count += 1

            result = announcements[:max_items]
            logger.info(f"K-Startup 크롤링 완료: 총 {len(result)}건")
            return result

        except Exception as e:
            logger.error(f"K-Startup 크롤링 오류: {e}")
            return []
        finally:
            await page.close()

    async def _parse_page(self, page: Page) -> list[Announcement]:
        """현재 페이지의 공고 파싱"""
        announcements = []

        # K-Startup 공고 리스트: div#bizPbancList 안의 li 요소들
        items = await page.query_selector_all("div#bizPbancList li")

        if not items:
            # 대안 선택자 시도
            items = await page.query_selector_all("[onclick*='go_view']")

        for item in items:
            try:
                ann = await self._parse_item(item)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"아이템 파싱 오류: {e}")
                continue

        return announcements

    async def _parse_item(self, item) -> Optional[Announcement]:
        """공고 아이템 파싱"""
        # HTML에서 공고 ID 추출 (go_view_blank 또는 go_view 패턴)
        inner_html = await item.inner_html()
        match = re.search(r'go_view(?:_blank)?\((\d+)\)', inner_html)

        if not match:
            return None

        pbancSn = match.group(1)

        # 제목 추출: input[name="scrap_list_bizPbancNm"]의 value
        title_input = await item.query_selector('input[name="scrap_list_bizPbancNm"]')
        if title_input:
            title = await title_input.get_attribute("value")
        else:
            # 대안: 텍스트에서 추출
            title_elem = await item.query_selector("strong.tit, .card_title, a.tit")
            title = await title_elem.inner_text() if title_elem else ""

        title = title.strip() if title else ""
        if not title:
            return None

        # D-day 추출 (span.flag.day 클래스)
        d_day = None
        d_day_elem = await item.query_selector("span.flag.day")
        if d_day_elem:
            d_day_text = await d_day_elem.inner_text()
            d_day_match = re.search(r'D-?(\d+)', d_day_text)
            if d_day_match:
                d_day = int(d_day_match.group(1))

        # 마감일 계산
        deadline = None
        if d_day is not None:
            from datetime import timedelta
            deadline = datetime.now() + timedelta(days=d_day)

        # 카테고리/분야 추출 (span.flag_agency 등)
        category = None
        # 여러 flag 타입 시도
        for selector in ["span.flag_agency", "span.flag.type01", "span.flag.type03", "span.flag.type04"]:
            cat_elem = await item.query_selector(selector)
            if cat_elem:
                category = (await cat_elem.inner_text()).strip()
                break

        # 주관기관 추출
        organization = None
        org_elem = await item.query_selector("span.agency, div.agency, p.agency")
        if org_elem:
            organization = (await org_elem.inner_text()).strip()

        # 상세 URL 생성
        url = self.config["detail_url_template"].format(id=pbancSn)

        return Announcement(
            id=pbancSn,
            source=self.source,
            title=title,
            category=category,
            deadline=deadline,
            d_day=d_day,
            organization=organization,
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
            await asyncio.sleep(1)

            # 상세 내용 추출
            content_elem = await page.query_selector(".view_cont, .detail_content, .content")
            content = await content_elem.inner_text() if content_elem else ""

            # 첨부파일 목록
            attachments = []
            file_links = await page.query_selector_all("a[href*='download'], a.file_down, .file_list a")
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
            logger.error(f"K-Startup 상세 조회 오류 ({announcement_id}): {e}")
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
        logger.info("Playwright 브라우저 종료됨")
