"""산업통상자원부(MOTIE) 크롤러 - Playwright 기반

JS 핸들러 사용하므로 Playwright 필수.
공고 목록 테이블에서 공고번호, 제목, 담당부서, 등록일, 첨부파일 등 추출.
첨부파일 직접 다운로드 링크: /attach/down/{hash1}/{hash2} (주로 HWP 파일)
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
    logger.warning("Playwright not available - MOTIE crawler disabled")


class MotieCrawler(BaseCrawler):
    """산업통상자원부 크롤러 (JS 렌더링)"""

    source = Source.MOTIE
    name = "MOTIE"

    def __init__(self):
        self.config = CRAWL_SOURCES["motie"]
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
            logger.info("MOTIE Playwright 브라우저 시작됨")

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """공고 목록 크롤링"""
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright not available for MOTIE")
            return []

        await self._init_browser()
        page = await self.browser.new_page()

        try:
            await page.goto(self.config["list_url"], wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3)

            # 리다이렉트 후 실제 URL 확인 (motir.go.kr로 리다이렉트될 수 있음)
            current_url = page.url
            logger.info(f"MOTIE 실제 URL: {current_url}")

            announcements = []
            page_num = 1
            max_pages = 5

            while len(announcements) < max_items and page_num <= max_pages:
                items = await self._parse_page(page)
                new_items = [i for i in items if i.id not in {a.id for a in announcements}]
                announcements.extend(new_items)

                logger.info(f"MOTIE 페이지 {page_num}: {len(new_items)}건 추가 (총 {len(announcements)}건)")

                if not new_items:
                    break

                next_clicked = await self._go_next_page(page, page_num)
                if not next_clicked:
                    break

                await asyncio.sleep(3)
                page_num += 1

            result = announcements[:max_items]
            logger.info(f"MOTIE 크롤링 완료: 총 {len(result)}건")
            return result

        except Exception as e:
            logger.error(f"MOTIE 크롤링 오류: {e}")
            return []
        finally:
            await page.close()

    async def _parse_page(self, page: Page) -> list[Announcement]:
        """현재 페이지의 공고 파싱

        테이블 구조: 공고번호, 제목, 담당부서, 등록일, 조회수, 첨부파일
        """
        announcements = []

        rows = await page.query_selector_all("table tbody tr")
        if not rows:
            # 대체 셀렉터
            rows = await page.query_selector_all(".board_list tbody tr, .list_table tbody tr")

        for row in rows:
            try:
                ann = await self._parse_row(row, page)
                if ann:
                    announcements.append(ann)
            except Exception as e:
                logger.debug(f"MOTIE 행 파싱 오류: {e}")

        return announcements

    async def _parse_row(self, row, page: Page) -> Optional[Announcement]:
        """테이블 행 파싱"""
        text = await row.inner_text()
        if not text or "등록된" in text or "게시물" in text:
            return None

        cells = await row.query_selector_all("td")
        if len(cells) < 4:
            return None

        inner_html = await row.inner_html()

        # 공고번호 (첫 번째 셀) - YYYY-NNN 형식
        notice_num = (await cells[0].inner_text()).strip()

        # 제목 (두 번째 셀)
        title_cell = cells[1]
        title_link = await title_cell.query_selector("a")
        if title_link:
            title = (await title_link.inner_text()).strip()
        else:
            title = (await title_cell.inner_text()).strip()

        if not title:
            return None

        # 헤더 행 스킵
        if title in ["제목", "공고명", "사업명"]:
            return None

        # ID 추출
        ann_id = None

        # onclick이나 href에서 ID 추출
        if title_link:
            onclick = await title_link.get_attribute("onclick") or ""
            href = await title_link.get_attribute("href") or ""

            # article.view(id) 또는 fn_view(id) 패턴
            id_match = re.search(
                r"(?:article\.view|fn_view|goView|fn_detail)\s*\(\s*'?([^')]+)'?\s*\)",
                onclick,
            )
            if id_match:
                ann_id = id_match.group(1)

            if not ann_id:
                # href에서 ID 추출
                href_match = re.search(r'/([A-Za-z0-9]+)(?:\?|$)', href)
                if href_match:
                    ann_id = href_match.group(1)

        # 공고번호를 ID로 사용
        if not ann_id and notice_num and re.match(r'\d{4}-\d+', notice_num):
            ann_id = notice_num.replace("-", "")

        if not ann_id:
            ann_id = hashlib.md5(title.encode()).hexdigest()[:12]

        # 담당부서 (세 번째 셀)
        dept = None
        if len(cells) >= 3:
            dept = (await cells[2].inner_text()).strip()

        # 등록일 (네 번째 셀)
        posted_date = None
        if len(cells) >= 4:
            date_text = (await cells[3].inner_text()).strip()
            for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
                try:
                    posted_date = datetime.strptime(date_text, fmt)
                    break
                except ValueError:
                    continue

        # 첨부파일 다운로드 링크 추출
        attach_links = await row.query_selector_all("a[href*='/attach/down/'], a[href*='download']")
        attachments = []
        for link in attach_links:
            href = await link.get_attribute("href") or ""
            name = (await link.inner_text()).strip()
            if href:
                attachments.append({"name": name or "첨부파일", "url": href})

        # 상세 URL 생성
        current_url = page.url
        base = self.config.get("detail_base", "")
        if not base:
            # 현재 URL에서 base 추출
            url_match = re.match(r'(https?://[^/]+)', current_url)
            base = url_match.group(1) if url_match else self.config["base_url"]

        # 상세 URL: 제목 링크의 href 사용
        detail_url = None
        if title_link:
            href = await title_link.get_attribute("href") or ""
            if href and href.startswith("http"):
                detail_url = href
            elif href and href.startswith("/"):
                detail_url = base + href

        if not detail_url:
            detail_url = f"{base}/kor/article/ATCL2826a2625/{ann_id}"

        return Announcement(
            id=ann_id,
            source=self.source,
            title=title,
            category=notice_num if re.match(r'\d{4}-\d+', notice_num or "") else None,
            deadline=None,
            d_day=None,
            department="산업통상자원부",
            organization=dept or "MOTIE",
            url=detail_url,
            posted_date=posted_date,
        )

    async def _go_next_page(self, page: Page, current_page: int) -> bool:
        """다음 페이지로 이동"""
        try:
            next_page = current_page + 1

            # article.list(pageIndex) JS 호출 시도
            try:
                await page.evaluate(f"article.list({next_page})")
                await page.wait_for_load_state("networkidle", timeout=10000)
                return True
            except Exception:
                pass

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
                        await next_btn.click()
                        await asyncio.sleep(2)
                        return True

            return False
        except Exception as e:
            logger.debug(f"MOTIE 다음 페이지 이동 실패: {e}")
            return False

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """공고 상세 정보 조회"""
        if not PLAYWRIGHT_AVAILABLE:
            return None

        await self._init_browser()
        page = await self.browser.new_page()

        try:
            base = self.config.get("detail_base", self.config["base_url"])
            url = f"{base}/kor/article/ATCL2826a2625/{announcement_id}"
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            # 상세 내용 추출
            content_elem = await page.query_selector(
                ".view_cont, .content, .board_view, .detail_wrap, "
                ".article_view, .bbs_view_cont"
            )
            content = await content_elem.inner_text() if content_elem else ""

            # 첨부파일 목록 (HWP 파일 직접 다운로드 링크, Download 대소문자 모두 매칭)
            attachments = []
            file_links = await page.query_selector_all(
                "a[href*='/attach/down/'], a[href*='Download'], a[href*='download'], "
                "a[href*='fileDown'], a.file_down, .file_list a, .attach a"
            )
            for link in file_links:
                name = (await link.inner_text()).strip()
                href = await link.get_attribute("href")
                if name and href:
                    if not href.startswith("http"):
                        href = base + href
                    attachments.append({"name": name, "url": href})

            return {
                "content": content[:5000],
                "attachments": attachments,
            }
        except Exception as e:
            logger.error(f"MOTIE 상세 조회 오류 ({announcement_id}): {e}")
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
        logger.info("MOTIE Playwright 브라우저 종료됨")
