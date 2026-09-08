"""Playwright 크롤러 브라우저 재사용 회귀 테스트

chromium 프로세스가 죽으면 self.browser 객체는 남아있지만 연결이 끊긴다.
_init_browser가 `is None`만 검사하면 죽은 객체를 재사용해 이후 모든
상세 페이지 수집이 실패한다(→ Bedrock이 제목/메타데이터만 보고 판단).
연결 상태(is_connected)까지 확인해 재시작하는지 검증한다.
"""
import pytest

from src.gov_funding.crawlers.iitp_crawler import IitpCrawler
from src.gov_funding.crawlers.kstartup_crawler import KStartupCrawler
from src.gov_funding.crawlers.motie_crawler import MotieCrawler
from src.gov_funding.crawlers.msit_crawler import MsitCrawler

CRAWLERS = [
    ("src.gov_funding.crawlers.kstartup_crawler", KStartupCrawler),
    ("src.gov_funding.crawlers.iitp_crawler", IitpCrawler),
    ("src.gov_funding.crawlers.msit_crawler", MsitCrawler),
    ("src.gov_funding.crawlers.motie_crawler", MotieCrawler),
]


class FakeBrowser:
    """연결 상태를 조작할 수 있는 가짜 Browser"""

    def __init__(self, connected: bool = True):
        self._connected = connected

    def is_connected(self) -> bool:
        return self._connected

    async def close(self):
        self._connected = False


class FakeChromium:
    def __init__(self, counter: dict):
        self._counter = counter

    async def launch(self, **kwargs):
        self._counter["launch"] += 1
        return FakeBrowser()


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
    return {"start": 0, "launch": 0}


def _patch(monkeypatch, module_path: str, counter: dict):
    import importlib

    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "PLAYWRIGHT_AVAILABLE", True)
    monkeypatch.setattr(
        module, "async_playwright", lambda: FakePlaywrightFactory(counter), raising=False
    )


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_launches_browser_when_none(monkeypatch, counter, module_path, crawler_cls):
    """browser가 없으면 playwright를 시작하고 chromium을 실행한다"""
    _patch(monkeypatch, module_path, counter)
    crawler = crawler_cls()

    await crawler._init_browser()

    assert counter == {"start": 1, "launch": 1}
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

    assert counter == {"start": 1, "launch": 1}
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

    assert counter == {"start": 2, "launch": 2}


@pytest.mark.parametrize("module_path,crawler_cls", CRAWLERS)
@pytest.mark.asyncio
async def test_raises_without_playwright(monkeypatch, module_path, crawler_cls):
    """Playwright 미설치 시 RuntimeError"""
    import importlib

    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "PLAYWRIGHT_AVAILABLE", False)

    with pytest.raises(RuntimeError):
        await crawler_cls()._init_browser()
