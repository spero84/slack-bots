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


# --- 경쟁사 확인 ---

from src.aec_news.config import COMPETITORS  # noqa: E402


def comp_candidate(no, title, hits, excerpt=""):
    item = make_item(no, title=title)
    item.competitor_hits = list(hits)
    item.excerpt = excerpt
    return item


def test_competitor_schema_enum_and_prompt():
    a = comp_candidate(1, "아이디어정보기술 파트너스 데이", ["fobecon"], excerpt="페이컴스, 포비콘, 제호바 등 협력사")
    client = FakeBedrock(text_response({"items": [{"no": 1, "company": "fobecon", "score": 0.5}]}))
    out = rel.verify_competitors([a], COMPETITORS, threshold=0.5, client=client)
    body = client.bodies[0]
    enum = body["output_config"]["format"]["schema"]["properties"]["items"]["items"]["properties"]["company"]["enum"]
    assert enum[-1] == "none" and "fobecon" in enum and len(enum) == len(COMPETITORS) + 1
    prompt = body["messages"][0]["content"]
    assert "본문 발췌(제목에는 이름 없음): 페이컴스, 포비콘" in prompt
    assert "후보 회사: fobecon" in prompt
    assert "Searchdoc(서치독) 자체 소식이면" in prompt
    assert out.matched == [a] and a.competitor == "fobecon"


def test_competitor_none_and_low_score_rejected_missing_retried():
    a = comp_candidate(1, "Viktor Orbán says", ["viktor"])
    b = comp_candidate(2, "Egnyte passing mention", ["egnyte"])
    c = comp_candidate(3, "Trunk Tools raises $40M", ["trunktools"])
    client = FakeBedrock(
        text_response({"items": [{"no": 1, "company": "none", "score": 0.0},
                                 {"no": 2, "company": "egnyte", "score": 0.3}]}),
        text_response({"items": [{"no": 1, "company": "trunktools", "score": 0.95}]}),
    )
    out = rel.verify_competitors([a, b, c], COMPETITORS, threshold=0.5, client=client)
    assert out.rejected == [a, b]
    assert out.matched == [c] and c.competitor == "trunktools"
    assert not out.unjudged and not out.warnings


def test_competitor_failure_falls_back_to_distinctive_title_names():
    a = comp_candidate(1, "인포시즈, 한전과 전력망 AI 공개", ["infocz"])
    b = comp_candidate(2, "New provision rules for builders", ["provision"])   # 일반어 이름은 규칙으로 판정하지 않는다
    out = rel.verify_competitors([a, b], COMPETITORS, client=FakeBedrock(RuntimeError("down")))
    assert out.matched == [a] and out.unjudged == [b]
    assert out.warnings and not out.llm_ok


def test_summarize_reports_already_sent_only_with_recent():
    a, b = make_item(1, score=0.9), make_item(2, score=0.8)
    client = FakeBedrock(text_response({"items": [
        {"no": 1, "summary": "A", "same_story_as": 1, "already_sent": True},
        {"no": 2, "summary": "B", "same_story_as": 2, "already_sent": False},
    ]}))
    repeats = []
    recent = [{"title": "옛 기사", "outlet": "동아일보", "region": "KR", "day": "11/12"}]
    rel.summarize([a, b], client=client, recent=recent, repeats=repeats)
    assert repeats == [a]
    assert "(KR) 동아일보 | 11/12 | 옛 기사" in client.bodies[0]["messages"][0]["content"]
    # recent가 없으면 already_sent를 믿지 않는다
    client2 = FakeBedrock(text_response({"items": [{"no": 1, "summary": "A", "same_story_as": 1, "already_sent": True}]}))
    repeats2 = []
    rel.summarize([make_item(3, score=0.9)], client=client2, recent=[], repeats=repeats2)
    assert repeats2 == []


def test_competitor_summary_groups_across_regions_but_not_across_companies():
    bw = make_item(1, score=1.0, region=Region.GLOBAL, outlet_key="web:www.businesswire.com", outlet_name="Business Wire")
    forbes = make_item(2, score=0.9, region=Region.US, outlet_key="forbes", outlet_name="Forbes")
    other = make_item(3, score=0.8)
    bw.competitor = forbes.competitor = "beamai"
    other.competitor = "planby"
    client = FakeBedrock(text_response({"items": [
        {"no": 1, "summary": "A", "same_story_as": 1, "already_sent": False},
        {"no": 2, "summary": "A", "same_story_as": 1, "already_sent": False},   # 국가 달라도 같은 회사 → 묶음
        {"no": 3, "summary": "B", "same_story_as": 1, "already_sent": False},   # 다른 회사 → 단독
    ]}))
    rel.summarize([bw, forbes, other], client=client, same_competitor=True)
    assert (bw.story, forbes.story, other.story) == (1, 1, 3)
    prompt = client.bodies[0]["messages"][0]["content"]
    assert "경쟁사가 다르면 묶지 않는다" in prompt and "경쟁사: Beam AI (Attentive.ai)" in prompt


def test_topic_summary_still_requires_same_region():
    kr, us = make_item(1, score=0.9), make_item(2, score=0.8, region=Region.US)
    client = FakeBedrock(text_response({"items": [
        {"no": 1, "summary": "A", "same_story_as": 1, "already_sent": False},
        {"no": 2, "summary": "A", "same_story_as": 1, "already_sent": False},
    ]}))
    rel.summarize([kr, us], client=client)
    assert (kr.story, us.story) == (1, 2)
    assert "KR/US/SG/GLOBAL" in client.bodies[0]["messages"][0]["content"]


def test_primepoint_is_never_attributed_by_title_rule():
    item = comp_candidate(1, "PrimePoint launches payroll compliance tool for construction contractors", ["primepoint"])
    out = rel.verify_competitors([item], COMPETITORS, client=FakeBedrock(RuntimeError("down")))
    assert out.matched == [] and out.unjudged == [item]
