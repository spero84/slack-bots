"""텍스트 정리 유틸리티"""
import html
import re

_BR_PATTERN = re.compile(r"<\s*/?\s*br\s*/?\s*>", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^<>]+>")


def clean_text(text: str | None) -> str:
    """HTML 태그/엔티티가 섞인 텍스트를 일반 텍스트로 정리

    상세 페이지나 첨부문서에서 온 텍스트는 이스케이프된 HTML('&lt;br /&gt;', '&nbsp;')을
    포함할 수 있고, 모델이 이를 요약에 그대로 옮기기도 한다.
    이중 이스케이프('&amp;lt;')까지 풀고, <br>은 줄바꿈으로, 나머지 태그는 제거한다.
    """
    if not text:
        return ""

    for _ in range(2):
        text = html.unescape(text)
    text = _BR_PATTERN.sub("\n", text)
    text = _TAG_PATTERN.sub("", text)
    text = text.replace("\xa0", " ").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
