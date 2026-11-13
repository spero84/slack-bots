"""aec-news 검색 범위 정리 HTML 테스트 (검색어 → 읽기 쉬운 표현, 표에 설정이 모두 나오는지)"""
from datetime import datetime, timezone

from src.aec_news.config import COMPETITORS, OUTLETS
from src.aec_news.scope_report import build_scope_html, readable


def test_readable_query_formatting():
    assert readable("(intitle:AI OR intitle:인공지능) (intitle:건설 OR intitle:시공) when:2d") == "AI · 인공지능 × 건설 · 시공"
    assert readable('"Document Crunch"') == "Document Crunch"
    assert readable('"SWAPP" (architects OR Revit) -obituary -"Swapp Design"') == \
        "SWAPP × architects · Revit  (제외: obituary, Swapp Design)"
    assert readable('(intitle:"문서 AI" OR intitle:IDP)') == "문서 AI · IDP"


def test_scope_html_lists_every_outlet_and_competitor():
    page = build_scope_html(datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc))
    assert "AEC-SCOPE-20261113" in page and "07:30 수집 시작 → 08:30 KST 게시" in page
    for o in OUTLETS.values():
        assert o.domain in page, o.key
    for c in COMPETITORS:
        assert c.name.replace("&", "&amp;") in page, c.key
    assert "<script" not in page
