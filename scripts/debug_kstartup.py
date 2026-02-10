#!/usr/bin/env python3
"""K-Startup 페이지 구조 분석"""
import asyncio
import re
from playwright.async_api import async_playwright


async def analyze_kstartup():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        url = "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do"
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)

        # 공고 리스트 아이템 찾기
        items = await page.query_selector_all("div#bizPbancList li")
        print(f"공고 아이템 수: {len(items)}")

        for i, item in enumerate(items[:5]):
            print(f"\n=== 공고 {i+1} ===")

            # 공고 ID (go_view_blank에서 추출)
            inner_html = await item.inner_html()
            id_match = re.search(r'go_view(?:_blank)?\((\d+)\)', inner_html)
            ann_id = id_match.group(1) if id_match else "N/A"

            # 제목 (input value에서 추출)
            title_input = await item.query_selector('input[name="scrap_list_bizPbancNm"]')
            title = await title_input.get_attribute("value") if title_input else "N/A"

            # D-day
            dday_elem = await item.query_selector("span.d-day, .dday, .d_day, span.date")
            dday_text = await dday_elem.inner_text() if dday_elem else ""

            # 카테고리/분야
            category_elem = await item.query_selector("span.flag_agency, span.category, span.flag")
            category = await category_elem.inner_text() if category_elem else ""

            # 기관
            org_elem = await item.query_selector("span.agency, div.agency, p.agency")
            org = await org_elem.inner_text() if org_elem else ""

            print(f"ID: {ann_id}")
            print(f"제목: {title}")
            print(f"D-day: {dday_text}")
            print(f"분야: {category.strip()}")
            print(f"기관: {org.strip() if org else 'N/A'}")

        # 더 많은 요소 확인
        print("\n\n=== 추가 요소 탐색 ===")

        # 모든 span 클래스 확인
        spans = await page.query_selector_all("div#bizPbancList li span")
        classes = set()
        for span in spans[:50]:
            cls = await span.get_attribute("class")
            if cls:
                classes.add(cls)
        print(f"span 클래스들: {sorted(classes)}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(analyze_kstartup())
