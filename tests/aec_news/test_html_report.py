"""aec-news HTML 정리본 테스트 (섹션 구성, 이스케이프, 링크 안전, 빈 날)"""
from datetime import datetime

from src.aec_news.config import KST
from src.aec_news.html_report import build_html
from src.aec_news.models import Region, Theme
from src.aec_news.slack_notifier import DigestContext

from .conftest import make_item

CTX = DigestContext(run_at=datetime(2026, 11, 14, 8, 30, tzinfo=KST),
                    days_by_region={r: 2 for r in Region}, warnings=["Google News 검색 1/70회 실패"], repeats_skipped=3)


def test_sections_order_and_content():
    comp = make_item(1, title="인포시즈, 서울대와 산업 도면 AI", score=0.9, story=1, summary="요약 A",
                     url="https://www.aitimes.com/1")
    comp.competitor, comp.mention = "infocz", "body"
    copy = make_item(2, title="사본", score=0.5, story=1, outlet_name="머니투데이", url="https://www.mt.co.kr/2")
    copy.competitor = "infocz"
    kr = make_item(3, title="국내 기사", score=0.8, story=3, theme=Theme.DRAWING_BIM, url="https://www.donga.com/3")
    trade = make_item(4, title="업계지 기사", score=0.7, story=4, region=Region.TRADE_KR, outlet_key="etnews",
                      outlet_name="전자신문", theme=Theme.AEC_AI)
    infra = make_item(5, title="AI 데이터센터 수주", score=0.4)
    page = build_html([kr, trade], [infra], CTX, competitors=[comp, copy])
    assert page.index("C-01") < page.index("K-01") < page.index("T-01") < page.index("R-01")
    assert "AEC-20261114" in page and "2026년 11월 14일 (토)" in page
    assert ">인포시즈<" in page and "본문 언급" in page and "같은 소식" in page and ">머니투데이<" in page
    assert "AI × 도면/BIM" in page and "요약 A" in page
    assert "Google News 검색 1/70회 실패" in page and "3건은 뺐습니다" in page
    assert "새 기사 없음" in page          # 빈 섹션 안내 (미국 등)


def test_escapes_text_and_rejects_non_http_links():
    bad = make_item(1, title='<img src=x onerror=alert(1)>"', score=0.9, story=1,
                    url="javascript:alert(1)", summary="<b>요약</b>")
    page = build_html([bad], [], CTX)
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(1)&gt;&quot;" in page
    assert 'href="javascript:' not in page and 'href="#"' in page
    assert "&lt;b&gt;요약&lt;/b&gt;" in page


def test_empty_day_page():
    page = build_html([], [], DigestContext(run_at=CTX.run_at, days_by_region={Region.KR: 2}))
    assert "새 기사를 찾지 못했습니다" in page
    assert page.startswith("<!doctype html>") and "</html>" in page
