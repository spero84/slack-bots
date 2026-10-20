"""Playwright 크롤러(MSIT, MOTIE) 브라우저 재사용 회귀 테스트

근본 원인: chromium을 '--single-process'로 실행하면 브라우저 수명 동안 페이지를 하나만
지원해서, crawl()이 페이지를 닫은 뒤 상세 조회의 두 번째 new_page()에서 chromium이
크래시했다 ("Browser.new_page: Target page, context or browser has been closed").
is_connected() 재시작은 안전망일 뿐 — 재시작된 브라우저도 페이지 하나 뒤에 다시 죽었다.

- 실행 옵션에 '--single-process'가 없는지 검증
- chromium이 죽으면(self.browser 객체는 남고 연결만 끊김) is_connected()로 감지해 재시작
- 상세 조회 브라우저 경로: TargetClosedError 시 브라우저 재시작 후 1회 재시도, 페이지는 항상 닫음
- (chromium 설치 시) 실제 브라우저로 new_page/close 3회 반복 후에도 같은 브라우저가 살아 있는지
"""
import importlib

import pytest

from src.gov_funding.crawlers.motie_crawler import MotieCrawler
from src.gov_funding.crawlers.msit_crawler import MsitCrawler

try:
    from playwright._impl._errors import TargetClosedError
except ImportError:  # playwright 내부 경로가 바뀐 경우 같은 이름/메시지로 대체
    class TargetClosedError(Exception):
        pass

CRAWLERS = [
    ("src.gov_funding.crawlers.msit_crawler", MsitCrawler),
    ("src.gov_funding.crawlers.motie_crawler", MotieCrawler),
]

CLOSED_MESSAGE = "Browser.new_page: Target page, context or browser has been closed"


class FakePage:
    def __init__(self, browser: "FakeBrowser"):
        self._browser = browser
        self.url = None
        self.closed = False

    async def goto(self, url, **kwargs):
        if self._browser.goto_error is not None:
            raise self._browser.goto_error
        self.url = url

    async def content(self):
        return f"<html><body>{self.url}</body></html>"

    async def close(self):
        self.closed = True


class FakeBrowser:
    """연결 상태를 조작할 수 있는 가짜 Browser"""

    def __init__(self, connected: bool = True, crash_on_new_page: bool = False):
        self._connected = connected
        self.crash_on_new_page = crash_on_new_page
        self.goto_error = None
        self.pages: list[FakePage] = []

    def is_connected(self) -> bool:
        return self._connected

    async def new_page(self):
        if self.crash_on_new_page or not self._connected:
            self._connected = False  # chromium 프로세스 사망 재현
            raise TargetClosedError(CLOSED_MESSAGE)
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def close(self):
        self._connected = False


class FakeChromium:
    def __init__(self, counter: dict):
        self._counter = counter

    async def launch(self, **kwargs):
        self._counter["launch"] += 1
        self._counter["launch_kwargs"].append(kwargs)
        crash = self._counter["crashing_launches"] > 0
        if crash:
            self._counter["crashing_launches"] -= 1
        browser = FakeBrowser(crash_on_new_page=crash)
        self._counter["browsers"].append(browser)
        return browser


class FakePlaywright:
    def __init__(self, counter: dict):
        self.chromium = FakeChromium(counter)
        self._counter = counter

    async def stop(self):
        pass


class FakePlaywrightFactory:
    """async_playwright() 대체 — start() 호출 횟수를 센다"""

    def __init__(self, counter: dict):
        self._counter = counter

    async def start(self):
        self._counter["start"] += 1
        return FakePlaywright(self._counter)


@pytest.fixture
def counter():
    return {"start": 0, "launch": 0, "launch_kwargs": [], "crashing_launches": 0, "browsers": []}


def _patch(monkeypatch, module_path: str, counter: dict):
    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(
        module, "async_playwright", lambda: FakePlaywrightFactory(counter), raising=False
    )


def _counts(counter: dict) -> dict:
    return {"start": counter["start"], "launch": counter["launch"]}


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_launch_args_exclude_single_process(monkeypatch, counter, module_path, crawler_cls):
    """'--single-process'로 실행하면 두 번째 new_page()에서 chromium이 크래시한다"""
    _patch(monkeypatch, module_path, counter)

    await crawler_cls()._init_browser()

    kwargs = counter["launch_kwargs"][0]
    assert kwargs["headless"] is True
    assert "--single-process" not in kwargs["args"]
    for flag in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"):
        assert flag in kwargs["args"]


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_launches_browser_when_none(monkeypatch, counter, module_path, crawler_cls):
    """browser가 없으면 playwright를 시작하고 chromium을 실행한다"""
    _patch(monkeypatch, module_path, counter)
    crawler = crawler_cls()

    await crawler._init_browser()

    assert _counts(counter) == {"start": 1, "launch": 1}
    assert crawler.browser.is_connected()


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_reuses_connected_browser(monkeypatch, counter, module_path, crawler_cls):
    """연결된 브라우저는 재사용한다 (재실행 없음)"""
    _patch(monkeypatch, module_path, counter)
    crawler = crawler_cls()

    await crawler._init_browser()
    first = crawler.browser
    await crawler._init_browser()

    assert _counts(counter) == {"start": 1, "launch": 1}
    assert crawler.browser is first


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_relaunches_disconnected_browser(
    monkeypatch, counter, module_path, crawler_cls
):
    """연결이 끊긴 브라우저는 새로 실행한다 (playwright 드라이버는 재사용)"""
    _patch(monkeypatch, module_path, counter)
    crawler = crawler_cls()

    await crawler._init_browser()
    dead = crawler.browser
    dead._connected = False  # chromium 프로세스 사망 재현

    await crawler._init_browser()

    assert counter["launch"] == 2, "죽은 브라우저를 재사용했다"
    assert counter["start"] == 1, "playwright 드라이버를 불필요하게 재시작했다"
    assert crawler.browser is not dead
    assert crawler.browser.is_connected()


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_relaunches_after_close(monkeypatch, counter, module_path, crawler_cls):
    """close() 후 다시 초기화하면 playwright와 브라우저를 모두 새로 만든다"""
    _patch(monkeypatch, module_path, counter)
    crawler = crawler_cls()

    await crawler._init_browser()
    await crawler.close()

    assert crawler.browser is None
    assert crawler.playwright is None

    await crawler._init_browser()

    assert _counts(counter) == {"start": 2, "launch": 2}


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_raises_without_playwright(monkeypatch, module_path, crawler_cls):
    """Playwright 미설치 시 RuntimeError"""
    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "PLAYWRIGHT_AVAILABLE", False)

    with pytest.raises(RuntimeError):
        await crawler_cls()._init_browser()


# ---------------------------------------------------------------- 상세 조회 브라우저 경로


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_browser_fetch_retries_once_after_target_closed(
    monkeypatch, counter, module_path, crawler_cls, caplog
):
    """new_page()가 TargetClosedError면 브라우저를 재시작해 1회 재시도한다"""
    _patch(monkeypatch, module_path, counter)
    counter["crashing_launches"] = 1  # 첫 브라우저는 new_page에서 죽는다
    crawler = crawler_cls()

    with caplog.at_level("WARNING", logger="gov-funding-monitor"):
        html = await crawler._fetch_html_with_browser("https://example.com/view")

    assert html is not None and "https://example.com/view" in html
    assert _counts(counter) == {"start": 1, "launch": 2}
    assert crawler.browser is counter["browsers"][1]
    assert all(p.closed for p in crawler.browser.pages), "페이지를 닫지 않았다"
    assert any("재시도" in r.message for r in caplog.records if r.levelname == "WARNING")


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_browser_fetch_gives_up_after_second_crash(
    monkeypatch, counter, module_path, crawler_cls, caplog
):
    """재시도도 실패하면 None + WARNING (무한 재시작 없음)"""
    _patch(monkeypatch, module_path, counter)
    counter["crashing_launches"] = 5
    crawler = crawler_cls()

    with caplog.at_level("WARNING", logger="gov-funding-monitor"):
        assert await crawler._fetch_html_with_browser("https://example.com/view") is None

    assert counter["launch"] == 2
    assert any("실패" in r.message for r in caplog.records if r.levelname == "WARNING")


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_browser_fetch_closes_page_on_navigation_error(
    monkeypatch, counter, module_path, crawler_cls
):
    """goto 타임아웃 등 브라우저가 살아 있는 오류는 재시작하지 않고 페이지만 닫는다"""
    _patch(monkeypatch, module_path, counter)
    crawler = crawler_cls()
    await crawler._init_browser()
    crawler.browser.goto_error = TimeoutError("Timeout 60000ms exceeded")

    assert await crawler._fetch_html_with_browser("https://example.com/view") is None

    assert counter["launch"] == 1
    assert [p.closed for p in crawler.browser.pages] == [True]


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_crawl_then_details_reuse_one_browser(monkeypatch, counter, module_path, crawler_cls):
    """목록 페이지를 닫은 뒤 상세 페이지를 여러 번 열어도 같은 브라우저를 쓴다"""
    _patch(monkeypatch, module_path, counter)
    crawler = crawler_cls()

    await crawler._init_browser()
    page = await crawler.browser.new_page()  # crawl()의 목록 페이지
    await page.close()
    for i in range(3):
        assert await crawler._fetch_html_with_browser(f"https://example.com/{i}")

    assert counter["launch"] == 1
    assert len(crawler.browser.pages) == 4
    assert all(p.closed for p in crawler.browser.pages)


# ---------------------------------------------------------------- 실제 chromium (설치된 경우만)


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_real_chromium_survives_sequential_pages(module_path, crawler_cls):
    """실제 chromium을 크롤러 실행 옵션으로 띄워 new_page/close를 반복해도 죽지 않는다

    '--single-process'가 있으면 두 번째 new_page()에서 TargetClosedError로 실패한다.
    네트워크 없이 data: URL만 사용.
    """
    module = importlib.import_module(module_path)
    if not module.PLAYWRIGHT_AVAILABLE:
        pytest.skip("playwright 미설치")

    crawler = crawler_cls()
    try:
        try:
            await crawler._init_browser()
        except Exception as e:
            pytest.skip(f"chromium 실행 불가: {e}")
        first = crawler.browser

        for i in range(3):  # 목록 페이지 + 상세 페이지 2건을 차례로 열고 닫는다
            await crawler._init_browser()
            page = await crawler.browser.new_page()
            await page.goto(f"data:text/html,<p id=x>{i}</p>")
            assert await page.inner_text("#x") == str(i)
            await page.close()

        html = await crawler._fetch_html_with_browser("data:text/html,<p id=y>detail</p>")
        assert html is not None and "detail" in html

        assert crawler.browser is first, "페이지 사이에 브라우저가 크래시해 재시작됐다"
        assert crawler.browser.is_connected()
    finally:
        await crawler.close()
