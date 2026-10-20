"""clean_text 테스트"""
import pytest

from src.gov_funding.utils.text import clean_text


@pytest.mark.parametrize("raw,expected", [
    ("운영된다.</br>", "운영된다."),
    ("운영된다.&lt;/br&gt;", "운영된다."),
    ("A&amp;lt;br /&amp;gt;B", "A\nB"),
    ("첫 줄<br />둘째 줄", "첫 줄\n둘째 줄"),
    ("<p>R&amp;D 지원</p>", "R&D 지원"),
    ("공백&nbsp;&nbsp;정리\xa0됨", "공백 정리 됨"),
    ("<div data-hjsonver=\"1.0\">본문</div>", "본문"),
    ("a\n\n\n\nb", "a\n\nb"),
    ("작은따옴표 &#39;인용&#39;", "작은따옴표 '인용'"),
    ("부등호 3 < 5 그대로", "부등호 3 < 5 그대로"),
])
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_clean_text_empty(raw):
    assert clean_text(raw) == ""
