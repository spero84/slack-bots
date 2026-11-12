"""aec-news Bedrock 관련성 판정/요약 테스트 (응답 검증, 폴백, 같은 소식 번호 정리)"""
import io
import json

import pytest

from src.aec_news import relevance as rel
from src.aec_news.models import Region, Theme

from .conftest import make_item


class FakeBedrock:
    """invoke_model 응답을 순서대로 돌려주는 가짜 클라이언트 (Exception이면 raise)"""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.bodies = []

    def invoke_model(self, modelId, body, contentType, accept):
        self.bodies.append(json.loads(body))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return {"body": io.BytesIO(json.dumps(payload).encode())}


def text_response(obj, stop_reason="end_turn"):
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}],
            "stop_reason": stop_reason, "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_invoke_json_request_shape_and_parse():
    client = FakeBedrock(text_response({"items": []}))
    assert rel.invoke_json("p", rel.TRIAGE_SCHEMA, 100, client) == {"items": []}
    body = client.bodies[0]
    assert body["thinking"] == {"type": "disabled"}
    assert "temperature" not in body
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_invoke_json_skips_thinking_block():
    resp = text_response({"items": []})
    resp["content"].insert(0, {"type": "thinking", "thinking": "..."})
    assert rel.invoke_json("p", rel.TRIAGE_SCHEMA, 100, FakeBedrock(resp)) == {"items": []}


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_invoke_json_raises_on_bad_stop(stop):
    with pytest.raises(RuntimeError):
        rel.invoke_json("p", rel.TRIAGE_SCHEMA, 100, FakeBedrock(text_response({"items": []}, stop)))


def test_triage_sorts_into_main_infra_rejected():
    items = [make_item(1), make_item(2), make_item(3), make_item(4)]
    client = FakeBedrock(
        text_response({"items": [
            {"no": 1, "kind": "ai_tech", "theme": "drawing_bim", "score": 0.95},
            {"no": 2, "kind": "ai_infra", "theme": "none", "score": 0.4},
            {"no": 3, "kind": "ai_tech", "theme": "aec_ai", "score": 0.5},   # 임계값 미달
            {"no": 99, "kind": "ai_tech", "theme": "aec_ai", "score": 1.0},  # 없는 번호
            # 4번 누락 → 4번만 다시 묻는다
        ]}),
        text_response({"items": [{"no": 1, "kind": "irrelevant", "theme": "none", "score": 0.0}]}),
    )
    out = rel.triage(items, threshold=0.6, client=client)
    assert [i.google_id for i in out.main] == ["CBMi0001"]
    assert out.main[0].theme is Theme.DRAWING_BIM
    assert [i.google_id for i in out.infra] == ["CBMi0002"]
    assert {i.google_id for i in out.rejected} == {"CBMi0003", "CBMi0004"}
    assert "[1] (KR) 동아일보 | 2026-11-12 | 기사 제목 4" in client.bodies[1]["messages"][0]["content"]
    assert out.llm_ok and not out.warnings and not out.unjudged


def test_triage_items_still_missing_after_retry_are_unjudged():
    items = [make_item(1, title="현대건설 생성형 AI로 도면 검토"), make_item(2)]
    client = FakeBedrock(
        text_response({"items": [{"no": 2, "kind": "irrelevant", "theme": "none", "score": 0.0},
                                 {"no": 2, "kind": "ai_tech", "theme": "aec_ai", "score": 0.9}]}),  # 중복, 1번 누락
        text_response({"items": []}),
    )
    out = rel.triage(items, threshold=0.6, client=client)
    assert [i.google_id for i in out.unjudged] == ["CBMi0001"]     # 탈락 캐시 대상(rejected)이 아니다
    assert [i.google_id for i in out.rejected] == ["CBMi0002"]
    assert out.main == []
    assert out.warnings and "다음 실행에서 다시 판정" in out.warnings[0]


def test_triage_retry_failure_keeps_items_unjudged():
    items = [make_item(1), make_item(2)]
    client = FakeBedrock(text_response({"items": [{"no": 1, "kind": "ai_tech", "theme": "aec_ai", "score": 0.9}]}),
                         RuntimeError("throttled"))
    out = rel.triage(items, threshold=0.6, client=client)
    assert [i.google_id for i in out.main] == ["CBMi0001"]
    assert [i.google_id for i in out.unjudged] == ["CBMi0002"]


def test_triage_clamps_score_and_defaults_theme():
    item = make_item(1, themes=(Theme.EPC_AI,))
    client = FakeBedrock(text_response({"items": [{"no": 1, "kind": "ai_tech", "theme": "none", "score": 7}]}))
    out = rel.triage([item], threshold=0.6, client=client)
    assert out.main[0].score == 1.0
    assert out.main[0].theme is Theme.EPC_AI


def test_triage_failure_falls_back_to_keywords():
    good = make_item(1, title="삼성물산, AI로 건설 도면 자동 검토")
    infra = make_item(2, title="건설사들 AI 데이터센터 수주 경쟁")
    noise = make_item(3, title="AI 반도체 주가 급등")
    out = rel.triage([good, infra, noise], threshold=0.6, client=FakeBedrock(RuntimeError("throttled")))
    assert not out.llm_ok
    assert [i.google_id for i in out.main] == ["CBMi0001"]
    assert [i.google_id for i in out.infra] == ["CBMi0002"]
    assert [i.google_id for i in out.unjudged] == ["CBMi0003"] and out.rejected == []   # 규칙 탈락은 캐시하지 않는다
    assert out.warnings and "키워드 규칙" in out.warnings[0]


def test_triage_batches(monkeypatch):
    monkeypatch.setattr(rel, "TRIAGE_BATCH", 2)
    items = [make_item(i) for i in range(1, 4)]
    client = FakeBedrock(
        text_response({"items": [{"no": 1, "kind": "ai_tech", "theme": "aec_ai", "score": 0.9},
                                 {"no": 2, "kind": "irrelevant", "theme": "none", "score": 0.0}]}),
        text_response({"items": [{"no": 1, "kind": "ai_tech", "theme": "epc_rag", "score": 0.8}]}),
    )
    out = rel.triage(items, threshold=0.6, client=client)
    assert [i.google_id for i in out.main] == ["CBMi0001", "CBMi0003"]
    assert out.main[1].theme is Theme.EPC_RAG


def test_keyword_fallback_needs_both_terms():
    assert rel.keyword_fallback([make_item(1, title="BIM 소프트웨어 신제품 출시")]).main == []
    assert rel.keyword_fallback([make_item(1, title="Generative AI comes to construction sites")]).main
    # 'AI'가 단어 일부(MAIN, SAID)인 경우는 AI로 보지 않는다
    assert rel.keyword_fallback([make_item(1, title="Contractor SAID MAIN construction delayed")]).main == []


def test_summarize_sets_summary_and_story_chain():
    a = make_item(1, score=0.9)
    b = make_item(2, score=0.8)
    c = make_item(3, score=0.7)
    us = make_item(4, score=0.6, region=Region.US)
    client = FakeBedrock(text_response({"items": [
        {"no": 1, "summary": "A 요약", "same_story_as": 1},
        {"no": 2, "summary": "B 요약", "same_story_as": 1},
        {"no": 3, "summary": "C 요약", "same_story_as": 2},   # 2 → 1로 정리
        {"no": 4, "summary": "D 요약", "same_story_as": 1},   # 다른 국가 → 단독
    ]}))
    assert rel.summarize([a, b, c, us], client=client) is None
    assert (a.story, b.story, c.story, us.story) == (1, 1, 1, 4)
    assert a.summary_ko == "A 요약"


def test_summarize_forward_reference_is_solo():
    a = make_item(1, score=0.9)
    b = make_item(2, score=0.8)
    client = FakeBedrock(text_response({"items": [
        {"no": 1, "summary": "A", "same_story_as": 2},
        {"no": 2, "summary": "B", "same_story_as": 2},
    ]}))
    rel.summarize([a, b], client=client)
    assert a.story == 1 and b.story == 2


def test_summarize_failure_returns_warning():
    item = make_item(1, score=0.9)
    warn = rel.summarize([item], client=FakeBedrock(RuntimeError("boom")))
    assert warn and "요약" in warn
    assert item.summary_ko is None


def test_prompt_contains_region_outlet_and_snippet():
    item = make_item(1, title="AI가 도면을 읽는다", snippet="인포시즈와 서울대가 공동 개발")
    prompt = rel.build_triage_prompt([item])
    assert "[1] (KR) 동아일보 | 2026-11-12 | AI가 도면을 읽는다" in prompt
    assert "스니펫: 인포시즈와 서울대가 공동 개발" in prompt
