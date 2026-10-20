"""gov-funding Bedrock 클라이언트 인증 및 요청 본문 회귀 테스트

AWS_BEARER_TOKEN_BEDROCK(Claude CLI용 토큰)이 만료되면 boto3가 IAM Role 대신
그 토큰으로 인증을 시도해 InvokeModel이 AccessDeniedException으로 전부 실패한다.
모든 bedrock-runtime 클라이언트는 signature_version="v4"를 명시해 이를 회피한다.

또한 Sonnet 5 / Opus 5는 temperature를 거부하므로(ValidationException) 요청
본문에 temperature가 없어야 하고, thinking은 명시적으로 비활성화되어야 한다.
"""
import json

import pytest

from src.gov_funding.analyzers.bedrock_analyzer import BedrockAnalyzer
from src.gov_funding.storage.vector_storage import S3VectorStorage
from src.gov_funding.utils.config import Config


@pytest.fixture(autouse=True)
def _bedrock_env(monkeypatch):
    """만료된 bearer token이 설정된 상태를 재현"""
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "expired-dummy-token")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")


# --- 인증(signature_version) 테스트 ---

def test_analyzer_forces_sigv4():
    """분석기가 bearer token 대신 SigV4를 사용하는지"""
    assert BedrockAnalyzer().client.meta.config.signature_version == "v4"


def test_vector_storage_forces_sigv4():
    """벡터 저장소의 임베딩 클라이언트가 SigV4를 사용하는지"""
    assert S3VectorStorage().bedrock.meta.config.signature_version == "v4"


# --- 요청 본문 테스트 (Sonnet 5 호환) ---

def test_invoke_body_omits_temperature_and_disables_thinking(monkeypatch):
    """Sonnet 5는 temperature를 거부하므로 본문에 없어야 한다"""
    analyzer = BedrockAnalyzer()
    captured = {}

    class _FakeBody:
        @staticmethod
        def read():
            return json.dumps(
                {"content": [{"type": "text", "text": '{"relevance_score": 0.9}'}]}
            )

    def fake_invoke_model(modelId, body, **kwargs):
        captured["modelId"] = modelId
        captured["body"] = json.loads(body)
        return {"body": _FakeBody()}

    monkeypatch.setattr(analyzer.client, "invoke_model", fake_invoke_model)
    analyzer._invoke_model("테스트 프롬프트")

    assert "temperature" not in captured["body"]
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["body"]["max_tokens"] == 1024
    assert captured["body"]["output_config"]["format"]["type"] == "json_schema"


# --- 응답 파싱 테스트 (thinking 블록 대응) ---

def test_parse_response_with_leading_thinking_block():
    """thinking 블록이 앞에 와도 text 블록을 찾아 파싱한다"""
    response = {
        "content": [
            {"type": "thinking", "thinking": "이 공고는 AI 관련이다"},
            {
                "type": "text",
                "text": '{"relevance_score": 0.85, "summary": "AI 지원사업"}',
            },
        ]
    }
    score, summary = BedrockAnalyzer()._parse_response(response)
    assert score == 0.85
    assert summary == "AI 지원사업"


def test_parse_response_without_text_block_falls_back():
    """text 블록이 없으면 실패(None, None) — 0.5는 임계값 미만이라 조용히 제외됐었다"""
    response = {"content": [{"type": "thinking", "thinking": "..."}]}
    assert BedrockAnalyzer()._parse_response(response) == (None, None)


# --- 모델 ID 기본값 테스트 ---

def test_default_model_is_sonnet_5(monkeypatch):
    """크롤러 기본 모델이 Sonnet 5인지"""
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    assert Config().bedrock_model_id == "global.anthropic.claude-sonnet-5"


# --- 응답 파싱 견고성 테스트 (실제 실패 사례) ---

def _wrap(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def test_parse_response_with_code_fence():
    """마크다운 코드펜스로 감싼 응답도 파싱한다"""
    response = _wrap(
        '```json\n{"relevance_score": 0.9, "summary": "AI 바우처"}\n```'
    )
    assert BedrockAnalyzer()._parse_response(response) == (0.9, "AI 바우처")


def test_parse_response_with_nested_object():
    """중첩 객체가 있어도 파싱한다 (기존 regex는 실패했다)"""
    response = _wrap(
        '{"relevance_score": 0.8, "summary": "요약", "meta": {"k": "v"}}'
    )
    assert BedrockAnalyzer()._parse_response(response) == (0.8, "요약")


def test_parse_response_recovers_from_unescaped_quote():
    """요약문 내 따옴표로 JSON이 깨져도 점수/요약을 복구한다"""
    response = _wrap(
        '{\n  "relevance_score": 0.75,\n'
        '  "summary": "지원 대상은 "AI 스타트업"입니다",\n'
        '  "reason": "IT 분야"\n}'
    )
    score, summary = BedrockAnalyzer()._parse_response(response)
    assert score == 0.75
    assert summary == '지원 대상은 "AI 스타트업"입니다'


def test_parse_response_recovers_from_missing_comma():
    """콤마 누락으로 JSON이 깨져도 점수를 복구한다"""
    response = _wrap(
        '{\n  "relevance_score": 0.65\n  "summary": "요약문"\n}'
    )
    score, _ = BedrockAnalyzer()._parse_response(response)
    assert score == 0.65


def test_parse_response_unrecoverable_falls_back():
    """복구 불가한 응답은 실패(None, None) — 다음 실행에서 재시도"""
    response = _wrap("죄송하지만 평가할 수 없습니다.")
    assert BedrockAnalyzer()._parse_response(response) == (None, None)
