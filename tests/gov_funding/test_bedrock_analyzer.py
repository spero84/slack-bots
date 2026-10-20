"""bedrock_analyzer 테스트 — 요청 본문(structured outputs), 응답 파싱/폴백, 실패 규약,
_fetch_detail_content() 및 filter_with_bedrock() 통합 (네트워크/Bedrock 호출 없음)"""
import asyncio
import json
import logging
from datetime import date, datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.gov_funding.analyzers import BedrockUnavailableError as ExportedUnavailableError
from src.gov_funding.analyzers import bedrock_analyzer as ba
from src.gov_funding.analyzers.bedrock_analyzer import (
    BedrockAnalyzer,
    BedrockUnavailableError,
    _fetch_detail_content,
    extract_fields_fallback,
    filter_with_bedrock,
)
from src.gov_funding.crawlers.base_crawler import BaseCrawler
from src.gov_funding.storage import Announcement, Source


class FakeCrawler(BaseCrawler):
    """테스트용 크롤러"""

    source = Source.MSS
    name = "테스트"

    def __init__(self, detail_result=None, download_result=None):
        self._detail_result = detail_result
        self._download_result = download_result

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        return []

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        return self._detail_result

    async def download_attachment(self, url: str, timeout: int = 30) -> Optional[bytes]:
        return self._download_result


def _make_announcement(source=Source.MSS, ann_id="test-1", title="테스트 공고") -> Announcement:
    return Announcement(
        id=ann_id,
        source=source,
        title=title,
        url="https://example.com/1",
    )


# --- _fetch_detail_content 테스트 ---


@pytest.mark.asyncio
async def test_fetch_detail_content_with_content_only():
    """상세 페이지 본문만 있는 경우"""
    crawler = FakeCrawler(
        detail_result={"content": "공고 상세 내용입니다.", "attachments": []}
    )
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result == "공고 상세 내용입니다."


@pytest.mark.asyncio
async def test_fetch_detail_content_with_hwp_attachment():
    """HWP 첨부파일이 있는 경우 텍스트 추출"""
    crawler = FakeCrawler(
        detail_result={
            "content": "페이지 본문",
            "attachments": [
                {"name": "공고문.hwp", "url": "https://example.com/file.hwp"},
            ],
        },
        download_result=b"fake-hwp-bytes",
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="HWP에서 추출한 텍스트",
    ) as mock_extract:
        result = await _fetch_detail_content(crawler, ann)

    mock_extract.assert_called_once_with(b"fake-hwp-bytes", "공고문.hwp")
    assert "페이지 본문" in result
    assert "HWP에서 추출한 텍스트" in result


@pytest.mark.asyncio
async def test_fetch_detail_content_with_hwpx_attachment():
    """HWPX 첨부파일도 처리"""
    crawler = FakeCrawler(
        detail_result={
            "content": "",
            "attachments": [
                {"name": "문서.hwpx", "url": "https://example.com/file.hwpx"},
            ],
        },
        download_result=b"fake-hwpx-bytes",
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="HWPX 텍스트",
    ):
        result = await _fetch_detail_content(crawler, ann)

    assert result == "HWPX 텍스트"


@pytest.mark.asyncio
async def test_fetch_detail_content_skips_unsupported():
    """지원하지 않는 확장자의 첨부파일은 무시"""
    crawler = FakeCrawler(
        detail_result={
            "content": "본문",
            "attachments": [
                {"name": "이미지.png", "url": "https://example.com/img.png"},
                {"name": "압축.zip", "url": "https://example.com/file.zip"},
            ],
        },
    )
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result == "본문"


@pytest.mark.asyncio
async def test_fetch_detail_content_with_pdf_attachment():
    """PDF 첨부파일 텍스트 추출"""
    crawler = FakeCrawler(
        detail_result={
            "content": "페이지 본문",
            "attachments": [
                {"name": "공고문.pdf", "url": "https://example.com/file.pdf"},
            ],
        },
        download_result=b"fake-pdf-bytes",
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="PDF에서 추출한 텍스트",
    ) as mock_extract:
        result = await _fetch_detail_content(crawler, ann)

    mock_extract.assert_called_once_with(b"fake-pdf-bytes", "공고문.pdf")
    assert "페이지 본문" in result
    assert "PDF에서 추출한 텍스트" in result


@pytest.mark.asyncio
async def test_fetch_detail_content_with_docx_attachment():
    """DOCX 첨부파일 텍스트 추출"""
    crawler = FakeCrawler(
        detail_result={
            "content": "",
            "attachments": [
                {"name": "공고문.docx", "url": "https://example.com/file.docx"},
            ],
        },
        download_result=b"fake-docx-bytes",
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="DOCX 텍스트",
    ):
        result = await _fetch_detail_content(crawler, ann)

    assert result == "DOCX 텍스트"


@pytest.mark.asyncio
async def test_fetch_detail_content_only_first_supported_file():
    """지원 파일이 여러 개일 때 첫 번째만 처리"""
    call_count = 0

    class CountingCrawler(FakeCrawler):
        async def download_attachment(self, url, timeout=30):
            nonlocal call_count
            call_count += 1
            return b"bytes"

    crawler = CountingCrawler(
        detail_result={
            "content": "본문",
            "attachments": [
                {"name": "첫번째.hwp", "url": "https://example.com/1.hwp"},
                {"name": "두번째.hwp", "url": "https://example.com/2.hwp"},
            ],
        },
    )
    ann = _make_announcement()

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="텍스트",
    ):
        await _fetch_detail_content(crawler, ann)

    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_detail_content_download_failure():
    """다운로드 실패 시 본문만 반환"""
    crawler = FakeCrawler(
        detail_result={
            "content": "본문만 사용",
            "attachments": [
                {"name": "공고문.hwp", "url": "https://example.com/file.hwp"},
            ],
        },
        download_result=None,  # 다운로드 실패
    )
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result == "본문만 사용"


@pytest.mark.asyncio
async def test_fetch_detail_content_get_detail_returns_none():
    """get_detail()이 None을 반환하는 경우"""
    crawler = FakeCrawler(detail_result=None)
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result is None


@pytest.mark.asyncio
async def test_fetch_detail_content_exception_graceful():
    """예외 발생 시 None 반환 (graceful fallback)"""

    class ErrorCrawler(FakeCrawler):
        async def get_detail(self, announcement_id):
            raise RuntimeError("연결 오류")

    crawler = ErrorCrawler()
    ann = _make_announcement()

    result = await _fetch_detail_content(crawler, ann)

    assert result is None


# --- filter_with_bedrock 통합 테스트 ---


@pytest.mark.asyncio
async def test_filter_with_bedrock_passes_detail_content():
    """crawlers 제공 시 detail_content가 analyze_relevance에 전달됨"""
    ann = _make_announcement(source=Source.MSS)

    crawler = FakeCrawler(
        detail_result={"content": "상세 내용", "attachments": []},
    )
    crawlers = {"mss": crawler}

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.BedrockAnalyzer"
    ) as MockAnalyzer:
        mock_instance = MockAnalyzer.return_value
        mock_instance.analyze_relevance = AsyncMock(return_value=(0.9, "요약"))

        result = await filter_with_bedrock([ann], threshold=0.7, crawlers=crawlers)

    # detail_content가 전달되었는지 확인
    mock_instance.analyze_relevance.assert_called_once()
    call_args = mock_instance.analyze_relevance.call_args
    assert call_args[0][0] == ann
    assert call_args[0][1] == "상세 내용"  # detail_content


@pytest.mark.asyncio
async def test_filter_with_bedrock_no_crawlers():
    """crawlers 미제공 시 기존 동작 (detail_content=None)"""
    ann = _make_announcement(source=Source.BIZINFO)

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.BedrockAnalyzer"
    ) as MockAnalyzer:
        mock_instance = MockAnalyzer.return_value
        mock_instance.analyze_relevance = AsyncMock(return_value=(0.8, "요약"))

        result = await filter_with_bedrock([ann], threshold=0.7)

    call_args = mock_instance.analyze_relevance.call_args
    assert call_args[0][1] is None  # detail_content가 None


@pytest.mark.asyncio
async def test_filter_with_bedrock_source_not_in_crawlers():
    """소스가 crawlers에 없으면 detail_content=None"""
    ann = _make_announcement(source=Source.BIZINFO)

    crawlers = {"mss": FakeCrawler()}

    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.BedrockAnalyzer"
    ) as MockAnalyzer:
        mock_instance = MockAnalyzer.return_value
        mock_instance.analyze_relevance = AsyncMock(return_value=(0.8, "요약"))

        result = await filter_with_bedrock([ann], threshold=0.7, crawlers=crawlers)

    call_args = mock_instance.analyze_relevance.call_args
    assert call_args[0][1] is None


# =====================================================================
# 요청 본문 / 클라이언트 설정
# =====================================================================


@pytest.fixture
def _aws_env(monkeypatch):
    """boto3 클라이언트 생성용 더미 자격증명 (네트워크 호출 없음)"""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")


def _capture_invoke(analyzer: BedrockAnalyzer, monkeypatch, response: Optional[dict] = None) -> dict:
    captured: dict = {}
    payload = response or {
        "content": [{"type": "text", "text": '{"relevance_score":0.9,"summary":"요약","reason":"이유"}'}],
        "stop_reason": "end_turn",
    }

    class _Body:
        @staticmethod
        def read():
            return json.dumps(payload)

    def fake_invoke_model(modelId, body, **kwargs):
        captured["modelId"] = modelId
        captured["body"] = json.loads(body)
        captured["kwargs"] = kwargs
        return {"body": _Body()}

    monkeypatch.setattr(analyzer.client, "invoke_model", fake_invoke_model)
    return captured


def test_invoke_body_uses_structured_outputs(_aws_env, monkeypatch):
    """output_config.format json_schema + max_tokens 1024 + thinking disabled + temperature 없음"""
    analyzer = BedrockAnalyzer()
    captured = _capture_invoke(analyzer, monkeypatch)

    analyzer._invoke_model("프롬프트")
    body = captured["body"]

    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["max_tokens"] == 1024
    assert body["thinking"] == {"type": "disabled"}
    assert "temperature" not in body
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    schema = fmt["schema"]
    assert schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relevance_score": {"type": "number"},
            "summary": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["relevance_score", "summary", "reason"],
    }
    # structured outputs는 수치 제약을 지원하지 않는다 → 코드에서 보정
    assert "minimum" not in json.dumps(schema) and "maximum" not in json.dumps(schema)
    assert body["messages"] == [{"role": "user", "content": "프롬프트"}]


def test_client_config_sigv4_and_adaptive_retries(_aws_env):
    """SigV4 강제 + adaptive 재시도 (ServiceUnavailable/Throttling 대응)"""
    config = BedrockAnalyzer().client.meta.config
    assert config.signature_version == "v4"
    assert config.retries["mode"] == "adaptive"
    # botocore는 max_attempts(재시도 횟수)를 total_max_attempts(최초 시도 포함)로 변환한다
    assert config.retries.get("total_max_attempts", config.retries.get("max_attempts")) in (6, 7)


@pytest.mark.asyncio
async def test_analyze_relevance_end_to_end_with_structured_response(_aws_env, monkeypatch):
    """structured outputs 실응답(bedrock_run4) 형태가 그대로 파싱된다"""
    analyzer = BedrockAnalyzer()
    text = (
        '{"relevance_score":0.6,"summary":"공공데이터를 창업기업에 개방하는 협업 과제 참여기업 모집 공고입니다. '
        '과제별 최대 1억원을 지원합니다.","reason":"일반 창업지원에 가깝습니다."}'
    )
    _capture_invoke(
        analyzer,
        monkeypatch,
        {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"},
    )
    score, summary = await analyzer.analyze_relevance(_make_announcement())
    assert score == 0.6
    assert summary == "공공데이터를 창업기업에 개방하는 협업 과제 참여기업 모집 공고입니다. 과제별 최대 1억원을 지원합니다."


# =====================================================================
# 프롬프트
# =====================================================================


def test_prompt_rules_and_no_freeform_json_block(_aws_env, monkeypatch):
    monkeypatch.setattr(ba, "today_kst", lambda: date(2026, 10, 20))
    ann = _make_announcement()
    ann.deadline = datetime(2026, 10, 29, 23, 59, 59)

    prompt = BedrockAnalyzer()._build_prompt(ann, "상세 본문")

    assert "JSON 형식으로만 응답" not in prompt
    assert "HTML" in prompt and "마크다운 금지" in prompt
    assert "2-3문장 일반 텍스트" in prompt
    assert "오늘 날짜: 2026-10-20" in prompt
    assert "마감일: 2026-10-29" in prompt
    # 신청 불가 공고 규칙 + 감점하지 않는 예외
    assert "0.3 이하" in prompt
    for rule in ("선정 결과", "평가 결과", "단순 안내", "법령·제도 개정", "이미 마감된 공고"):
        assert rule in prompt
    for exempt in ("조달·용역 입찰", "주관기관·운영기관 모집", "해외 전시회", "해외 IT지원센터"):
        assert exempt in prompt
    # 헤더(마감일)가 상세 내용보다 앞에 온다
    assert prompt.index("마감일: 2026-10-29") < prompt.index("상세 본문")


def test_prompt_detail_budget_is_6000_chars(_aws_env):
    detail = "가" * 5995 + "끝부분마커" + "쀍" * 3000  # 정확히 6000자 뒤부터 '쀍'
    prompt = BedrockAnalyzer()._build_prompt(_make_announcement(), detail)
    assert "가" * 5995 + "끝부분마커" in prompt
    assert "쀍" not in prompt


# =====================================================================
# 응답 파싱 / 폴백
# =====================================================================

S = "선정 기업은 과제별 최대 1억원의 사업화 자금을 받는다."
R = "일반 창업기업 대상 지원사업이다."

# 실제 Slack에 누출된 요약(09-12~09-23)에서 복원한 깨진 구분자 형태
MALFORMED_SHAPES = {
    "missing_comma": f'"{S}"\n    "reason"',
    "semicolon": f'"{S}";\n    "reason"',
    "extra_quote": f'"{S}","\n    "reason"',
    "semicolon_comma": f'"{S}";,\n    "reason"',
    "inner_ascii_quotes": f'"{S[:10]} "B" 이상 {S[10:]}",\n    "reason"',
    "literal_newline_in_summary": f'"{S}\n둘째 줄.",\n    "reason"',
}


def _raw_model_text(separator: str, score: str = "0.7") -> str:
    return (
        '```json\n{\n    "relevance_score": ' + score + ',\n    "summary": '
        + separator + f': "{R}"\n}}\n```'
    )


def _resp(text: str, stop_reason: str = "end_turn", **extra) -> dict:
    return {"content": [{"type": "text", "text": text}], "stop_reason": stop_reason, **extra}


@pytest.mark.parametrize("shape", list(MALFORMED_SHAPES))
def test_parse_response_malformed_shapes_do_not_leak_reason(shape):
    score, summary = BedrockAnalyzer.__new__(BedrockAnalyzer)._parse_response(
        _resp(_raw_model_text(MALFORMED_SHAPES[shape]))
    )
    assert score == 0.7
    assert summary
    assert "reason" not in summary
    assert R not in summary
    assert not summary.endswith(('"', ";", ","))
    assert "\n" not in summary
    assert summary.startswith(S[:10])


def test_fallback_unescapes_and_clamps():
    score, summary = extract_fields_fallback(
        '{"relevance_score": 8, "summary": "지원 대상은 \\"AI 스타트업\\"\\n\\uc785\\ub2c8\\ub2e4"'
    )
    assert score == 1.0
    assert summary == '지원 대상은 "AI 스타트업"\n입니다'

    score, _ = extract_fields_fallback('{"relevance_score": -0.2, "summary": "x"}')
    assert score == 0.0
    score, _ = extract_fields_fallback('{"relevance_score": "0.85", "summary": "x"')
    assert score == 0.85


def test_fallback_summary_without_reason_key():
    score, summary = extract_fields_fallback('{"relevance_score": 0.8, "summary": "요약만 있음"\n}')
    assert (score, summary) == (0.8, "요약만 있음")


def test_parse_response_clamps_valid_json_score():
    analyzer = BedrockAnalyzer.__new__(BedrockAnalyzer)
    assert analyzer._parse_response(
        _resp('{"relevance_score": 1.7, "summary": "s", "reason": "r"}')
    ) == (1.0, "s")
    assert analyzer._parse_response(
        _resp('{"relevance_score": -1, "summary": "s", "reason": "r"}')
    ) == (0.0, "s")


def test_parse_response_sanitizes_summary_html():
    """모델이 HTML/엔티티를 요약에 옮겨도 일반 텍스트 한 줄로 정리된다"""
    analyzer = BedrockAnalyzer.__new__(BedrockAnalyzer)
    text = json.dumps(
        {"relevance_score": 0.8, "summary": "2년간 운영된다.&lt;/br&gt;\n실증&middot;구매&nbsp;지원<br />끝", "reason": "r"},
        ensure_ascii=False,
    )
    score, summary = analyzer._parse_response(_resp(text))
    assert score == 0.8
    assert summary == "2년간 운영된다. 실증·구매 지원 끝"
    for junk in ("<", ">", "&lt;", "&nbsp;", "&middot;", "\n"):
        assert junk not in summary


def test_parse_response_max_tokens_keeps_score_drops_summary(caplog):
    analyzer = BedrockAnalyzer.__new__(BedrockAnalyzer)
    truncated = '{"relevance_score":0.9,"summary":"선정 기업은 과제별 최대 1억원의 사업'
    with caplog.at_level(logging.WARNING):
        score, summary = analyzer._parse_response(
            _resp(truncated, stop_reason="max_tokens", usage={"output_tokens": 1024})
        )
    assert (score, summary) == (0.9, None)
    assert any("max_tokens" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_parse_response_refusal_is_failure():
    analyzer = BedrockAnalyzer.__new__(BedrockAnalyzer)
    response = _resp(
        '{"relevance_score": 0.9, "summary": "s", "reason": "r"}',
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "cyber"},
    )
    assert analyzer._parse_response(response) == (None, None)


def test_parse_response_unparseable_is_failure(caplog):
    analyzer = BedrockAnalyzer.__new__(BedrockAnalyzer)
    with caplog.at_level(logging.WARNING):
        assert analyzer._parse_response(_resp("죄송하지만 평가할 수 없습니다.")) == (None, None)
    assert any("응답 파싱 실패" in r.getMessage() for r in caplog.records)
    # 점수 없이 요약만 복구되면 판단 불가 → 실패
    assert analyzer._parse_response(_resp('{"summary": "요약만"')) == (None, None)
    assert analyzer._parse_response({"content": [], "stop_reason": "end_turn"}) == (None, None)


def test_parse_response_fallback_logs_raw_and_stop_reason(caplog):
    analyzer = BedrockAnalyzer.__new__(BedrockAnalyzer)
    with caplog.at_level(logging.WARNING):
        analyzer._parse_response(_resp(_raw_model_text(MALFORMED_SHAPES["semicolon"])))
    messages = [r.getMessage() for r in caplog.records]
    assert any("정규식으로 필드 복구" in m and "stop_reason=end_turn" in m and '"relevance_score"' in m for m in messages)


# =====================================================================
# analyze_relevance 실패 규약
# =====================================================================


@pytest.mark.asyncio
async def test_analyze_relevance_client_error_returns_none(_aws_env, monkeypatch):
    analyzer = BedrockAnalyzer()

    def raise_access_denied(prompt):
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Authentication failed"}},
            "InvokeModel",
        )

    monkeypatch.setattr(analyzer, "_invoke_model", raise_access_denied)
    assert await analyzer.analyze_relevance(_make_announcement()) == (None, None)


@pytest.mark.asyncio
async def test_analyze_relevance_unexpected_error_returns_none(_aws_env, monkeypatch):
    analyzer = BedrockAnalyzer()
    monkeypatch.setattr(analyzer, "_invoke_model", MagicMock(side_effect=TimeoutError("read timeout")))
    assert await analyzer.analyze_relevance(_make_announcement()) == (None, None)


def test_unavailable_error_exported():
    assert ExportedUnavailableError is BedrockUnavailableError
    assert issubclass(BedrockUnavailableError, RuntimeError)


# =====================================================================
# filter_with_bedrock 실패 집계
# =====================================================================


def _anns(n: int, source=Source.BIZINFO) -> list[Announcement]:
    return [_make_announcement(source=source, ann_id=f"a{i}", title=f"공고 {i}") for i in range(n)]


async def _run_filter(results: list, announcements=None, crawlers=None, threshold=0.7):
    """analyze_relevance가 results를 순서대로 반환하도록 패치하고 filter_with_bedrock 실행"""
    announcements = announcements if announcements is not None else _anns(len(results))
    with patch("src.gov_funding.analyzers.bedrock_analyzer.BedrockAnalyzer") as MockAnalyzer:
        mock = MockAnalyzer.return_value
        mock.analyze_relevance = AsyncMock(side_effect=results)
        try:
            out = await filter_with_bedrock(announcements, threshold=threshold, crawlers=crawlers)
            return out, mock.analyze_relevance, None
        except BedrockUnavailableError as e:
            return None, mock.analyze_relevance, e


@pytest.mark.asyncio
async def test_filter_raises_when_first_three_fail():
    out, analyze, err = await _run_filter([(None, None)] * 5)
    assert err is not None
    assert str(err) == "Bedrock 분석 실패 3/3건"
    assert analyze.call_count == 3  # 4번째부터는 호출하지 않는다


@pytest.mark.asyncio
async def test_filter_raises_on_real_access_denied(_aws_env, monkeypatch):
    """실제 analyze_relevance 경로: 모든 InvokeModel이 AccessDenied면 BedrockUnavailableError"""
    calls = []

    def raise_access_denied(self, prompt):
        calls.append(prompt)
        raise ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Authentication failed"}},
            "InvokeModel",
        )

    monkeypatch.setattr(BedrockAnalyzer, "_invoke_model", raise_access_denied)
    with pytest.raises(BedrockUnavailableError, match="3/3"):
        await filter_with_bedrock(_anns(10), threshold=0.7)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_filter_single_failure_drops_item_only(caplog):
    results = [(0.9, "s0"), (None, None), (0.8, "s2"), (0.5, "s3"), (0.95, "s4")]
    announcements = _anns(5)
    with caplog.at_level(logging.WARNING):
        out, analyze, err = await _run_filter(results, announcements)
    assert err is None
    assert [a.id for a in out] == ["a0", "a2", "a4"]
    assert announcements[1].relevance_score is None  # 실패 공고는 점수를 부여하지 않는다
    assert any("공고 1" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_filter_raises_when_half_or_more_fail_overall():
    results = [(0.9, "s"), (None, None), (None, None), (0.9, "s"), (None, None), (0.9, "s")]
    out, analyze, err = await _run_filter(results)
    assert err is not None and str(err) == "Bedrock 분석 실패 3/6건"
    assert analyze.call_count == 6


@pytest.mark.asyncio
async def test_filter_tolerates_minority_failures():
    results = [(0.9, "s"), (None, None), (0.9, "s"), (None, None), (0.9, "s"), (None, None), (0.9, "s")]
    out, _, err = await _run_filter(results)
    assert err is None
    assert len(out) == 4


@pytest.mark.asyncio
async def test_filter_two_failures_out_of_two_does_not_raise():
    """시도 3건 미만이면 '처음 3건 실패' 규칙도, 최종 '3건 이상' 규칙도 해당하지 않는다"""
    out, _, err = await _run_filter([(None, None), (None, None)])
    assert err is None and out == []


# =====================================================================
# filter_with_bedrock — 상세 내용/마감일
# =====================================================================


@pytest.fixture
def _fixed_today(monkeypatch):
    monkeypatch.setattr(ba, "today_kst", lambda: date(2026, 10, 20))
    monkeypatch.setattr(ba, "now_kst", lambda: datetime(2026, 10, 20, 9, 0, 0))


@pytest.mark.asyncio
async def test_filter_sets_deadline_from_detail(_fixed_today):
    ann = _make_announcement(source=Source.MSS)
    crawler = FakeCrawler(
        detail_result={
            "content": "공고번호: 제2026-1호\n□ 접수기간 : 2026. 10. 20.(화) ~ 11. 5.(목) 18:00까지",
            "attachments": [],
        }
    )
    out, analyze, err = await _run_filter([(0.9, "요약")], [ann], crawlers={"mss": crawler})
    assert err is None and out == [ann]
    assert ann.deadline == datetime(2026, 11, 5, 23, 59, 59)
    assert ann.d_day == 16
    assert "접수기간" in analyze.call_args[0][1]


@pytest.mark.asyncio
async def test_filter_skips_bedrock_for_expired_deadline(_fixed_today, caplog):
    ann = _make_announcement(source=Source.NIPA, title="마감된 공고")
    crawler = FakeCrawler(
        detail_result={"content": "신청기간: 2026.09.01 ~ 2026.09.30", "attachments": []}
    )
    with caplog.at_level(logging.INFO):
        out, analyze, err = await _run_filter([], [ann], crawlers={"nipa": crawler})
    assert err is None and out == []
    analyze.assert_not_called()  # 비용 절감: 마감 공고는 호출하지 않는다
    assert any("제외 (마감): 마감된 공고" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_filter_keeps_existing_deadline(_fixed_today):
    ann = _make_announcement(source=Source.MSS)
    ann.deadline = datetime(2026, 12, 31, 23, 59, 59)
    ann.d_day = 72
    crawler = FakeCrawler(
        detail_result={"content": "신청기간: 2026.09.01 ~ 2026.09.30", "attachments": []}
    )
    out, analyze, _ = await _run_filter([(0.9, "요약")], [ann], crawlers={"mss": crawler})
    assert out == [ann]
    assert ann.deadline == datetime(2026, 12, 31, 23, 59, 59)
    assert ann.d_day == 72


@pytest.mark.asyncio
async def test_filter_prompt_detail_order_and_budget(_fixed_today):
    """페이지 본문 → 첨부 공고문 순서, 6000자 예산 — 2000자 뒤의 신청기간도 프롬프트에 남는다"""
    ann = _make_announcement(source=Source.MSIT)
    page = "과학기술정보통신부 공고 제2026-1007호 붙임과 같이 공모합니다."
    attachment = "□ 사업개요 " + "가" * 2800 + "\n□ 신청기간 : ’26.10.21.(수) ~ ’26.11.17.(화) 17시까지\n" + "나" * 9000
    crawler = FakeCrawler(
        detail_result={
            "content": page,
            "attachments": [{"name": "공고문.hwpx [137.0 KB]", "url": "https://x/f"}],
        },
        download_result=b"bytes",
    )
    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value=attachment,
    ):
        out, analyze, _ = await _run_filter([(0.9, "요약")], [ann], crawlers={"msit": crawler})

    detail = analyze.call_args[0][1]
    assert detail.startswith(page)
    assert detail.index(page) < detail.index("[첨부 공고문]") < detail.index("□ 사업개요")
    assert "□ 신청기간 : ’26.10.21.(수) ~ ’26.11.17.(화)" in detail
    assert len(detail) <= ba.DETAIL_PROMPT_BUDGET
    assert ann.deadline == datetime(2026, 11, 17, 23, 59, 59)  # 잘리지 않은 전체 텍스트로 추출


@pytest.mark.asyncio
async def test_filter_detail_is_source_agnostic(_fixed_today):
    """모든 소스(kstartup~motie)에 같은 상세/마감일 흐름이 적용된다"""
    sources = list(Source)
    announcements = [_make_announcement(source=s, ann_id=f"id-{s.value}") for s in sources]
    crawlers = {
        s.value: FakeCrawler(detail_result={"content": "접수 마감일: 2026년 11월 3일", "attachments": []})
        for s in sources
    }
    out, analyze, err = await _run_filter([(0.9, "요약")] * len(sources), announcements, crawlers=crawlers)
    assert err is None and len(out) == len(sources)
    assert all(a.deadline == datetime(2026, 11, 3, 23, 59, 59) and a.d_day == 14 for a in announcements)


# =====================================================================
# _fetch_detail_content — 첨부 선택 / 정리
# =====================================================================


class RecordingCrawler(FakeCrawler):
    """download_attachment 호출 URL 기록, URL별 응답 지정"""

    def __init__(self, detail_result, downloads: dict):
        super().__init__(detail_result=detail_result)
        self.downloads = downloads
        self.requested: list[str] = []

    async def download_attachment(self, url: str, timeout: int = 30) -> Optional[bytes]:
        self.requested.append(url)
        return self.downloads.get(url)


@pytest.mark.asyncio
async def test_fetch_detail_accepts_size_suffixed_names():
    crawler = RecordingCrawler(
        {"content": "", "attachments": [{"name": "공고.hwpx [178.7 KB]", "url": "https://x/1"}]},
        {"https://x/1": b"hwpx"},
    )
    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value="첨부 텍스트",
    ) as mock_extract:
        result = await _fetch_detail_content(crawler, _make_announcement())
    mock_extract.assert_called_once_with(b"hwpx", "공고.hwpx [178.7 KB]")
    assert result == "첨부 텍스트"


@pytest.mark.asyncio
async def test_fetch_detail_prefers_gonggo_attachment():
    """참가신청서 양식이 먼저 와도 '공고'가 들어간 파일을 우선한다"""
    crawler = RecordingCrawler(
        {
            "content": "본문",
            "attachments": [
                {"name": "(붙임1) 참가신청서.hwp", "url": "https://x/form"},
                {"name": "안내.zip", "url": "https://x/zip"},
                {"name": "(수정본)공고문.pdf", "url": "https://x/notice"},
            ],
        },
        {"https://x/form": b"form", "https://x/notice": b"notice"},
    )
    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        side_effect=lambda b, name: f"{name} 텍스트",
    ):
        result = await _fetch_detail_content(crawler, _make_announcement())
    assert crawler.requested == ["https://x/notice"]
    assert result == "본문\n\n(수정본)공고문.pdf 텍스트"


@pytest.mark.asyncio
async def test_fetch_detail_tries_next_candidate_once():
    """우선 파일이 실패하면 다음 후보 1개까지만 시도한다"""
    crawler = RecordingCrawler(
        {
            "content": "본문",
            "attachments": [
                {"name": "공고문.hwp", "url": "https://x/1"},
                {"name": "공고문.pdf", "url": "https://x/2"},
                {"name": "별첨.pdf", "url": "https://x/3"},
            ],
        },
        {},  # 모두 다운로드 실패
    )
    result = await _fetch_detail_content(crawler, _make_announcement())
    assert result == "본문"
    assert crawler.requested == ["https://x/1", "https://x/2"]


@pytest.mark.asyncio
async def test_fetch_detail_cleans_html_and_keeps_multiline_brackets():
    crawler = RecordingCrawler(
        {
            "content": "&#39;도약&#39; 모집공고&nbsp;&lt;br /&gt;\n감사합니다.<br />",
            "attachments": [{"name": "공고.pdf", "url": "https://x/1"}],
        },
        {"https://x/1": b"pdf"},
    )
    attachment_text = "A < B 조건\n중요 내용: 신청자격\nC > D"
    with patch(
        "src.gov_funding.analyzers.bedrock_analyzer.extract_text_from_file",
        return_value=attachment_text,
    ):
        result = await _fetch_detail_content(crawler, _make_announcement())
    for junk in ("&#39;", "&nbsp;", "&lt;", "<br"):
        assert junk not in result
    assert "'도약' 모집공고" in result
    # 줄을 넘는 '<' … '>'가 태그로 오인되어 본문이 지워지지 않는다
    assert "중요 내용: 신청자격" in result
