"""aec-news 파이프라인 테스트 (중복 제거, 전송 이력, 탈락 캐시, 전송 실패 시 이력 미저장)

검색·판정·요약·전송을 가짜로 바꿔 run_workflow의 상태 처리만 검증한다.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.aec_news import main as m
from src.aec_news.google_news import SearchResult
from src.aec_news.models import Region, Theme
from src.aec_news.relevance import TriageOutcome
from src.aec_news.state import RunState

from .conftest import make_item

NOW = datetime(2026, 11, 12, 23, 30, tzinfo=timezone.utc)


class FakeClient:
    requests = 0
    tripped = None


class FakeNotifier:
    """ok=False면 본문 실패, fail_indices의 스레드 답글은 실패"""

    def __init__(self, ok=True, fail_indices=()):
        self.ok = ok
        self.fail_indices = set(fail_indices)
        self.sent = []

    def send(self, messages):
        self.sent.append(messages)
        if not self.ok:
            return []
        return [i for i in range(len(messages)) if i not in self.fail_indices]


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """search/triage/resolve/summarize를 가짜로. triage 규칙: 제목에 '관련'이 있으면 main, '인프라'면 infra."""
    found: list = []
    calls = {"triage": [], "summarize": 0}
    search_complete = {"value": True}

    def fake_search(now, days_by_region, hours=None, client=None):
        result = SearchResult(items=[i.model_copy(deep=True) for i in found], total=22)
        if not search_complete["value"]:
            result.failed = 3
            result.warnings.append("Google News 검색 3/22회 실패")
        return result

    def fake_triage(items, threshold):
        calls["triage"].append([i.title for i in items])
        out = TriageOutcome()
        for i in items:
            if "관련" in i.title:
                i.score, i.theme = 0.9, Theme.DRAWING_BIM
                out.main.append(i)
            elif "인프라" in i.title:
                i.score = 0.4
                out.infra.append(i)
            else:
                i.score = 0.1
                out.rejected.append(i)
        return out

    def fake_resolve(items, cache, client=None):
        for i in items:
            i.url = cache.setdefault(i.google_id, f"https://www.donga.com/news/{i.google_id}")
        return list(items), []

    def fake_summarize(items):
        calls["summarize"] += 1
        for n, i in enumerate(items, 1):
            i.summary_ko, i.story = f"요약 {n}", n
        return None

    monkeypatch.setattr(m, "search_all", fake_search)
    monkeypatch.setattr(m, "triage", fake_triage)
    monkeypatch.setattr(m, "resolve_urls", fake_resolve)
    monkeypatch.setattr(m, "summarize", fake_summarize)
    state_path = str(tmp_path / "state.json")

    class P:
        pass

    p = P()
    p.found, p.calls, p.state_path, p.search_complete = found, calls, state_path, search_complete
    p.state = lambda: RunState(state_path).load()
    return p


def run(p, notifier=None, **kw):
    return m.run_workflow(now=kw.pop("now", NOW), state=p.state(), client=FakeClient(),
                          notifier=notifier or FakeNotifier(), **kw)


def test_dedupe_run_merges_same_article_and_themes():
    a = make_item(1, themes=(Theme.DRAWING_BIM,))
    b = make_item(1, themes=(Theme.AEC_AI,))                       # 같은 Google ID
    c = make_item(2, title=a.title, themes=(Theme.AEC_RAG,))       # 같은 언론사·같은 제목, 다른 ID
    d = make_item(3, title=a.title, outlet_key="mk", outlet_name="매일경제")   # 다른 언론사
    out = m.dedupe_run([a, b, c, d])
    assert out == [a, d]
    assert a.query_themes == [Theme.DRAWING_BIM, Theme.AEC_AI, Theme.AEC_RAG]


def test_dedupe_resolved_by_canonical_url():
    a = make_item(1, url="https://www.mt.co.kr/future/2026/11/10/1")
    b = make_item(2, title="다른 제목", url="https://www.mt.co.kr/amp/future/2026/11/10/1?utm_source=x")
    c = make_item(3, title="URL 없음")
    assert m.dedupe_resolved([a, b, c]) == [a, c]


def test_full_run_sends_and_records_history(pipeline):
    pipeline.found += [make_item(1, title="관련 도면 AI"), make_item(2, title="인프라 데이터센터"),
                       make_item(3, title="무관한 기사")]
    notifier = FakeNotifier()
    result = run(pipeline, notifier)
    assert result["notification"] is True
    assert [i["title"] for i in result["main"]] == ["관련 도면 AI"]
    assert [i["title"] for i in result["infra"]] == ["인프라 데이터센터"]
    assert len(notifier.sent) == 1

    state = pipeline.state()
    assert state.last_success == NOW
    assert state.was_sent([make_item(1, title="관련 도면 AI").title_key])
    assert state.is_rejected(make_item(3, title="무관한 기사").title_key, NOW)
    assert state.decode_cache()["CBMi0001"].startswith("https://www.donga.com/")


def test_second_run_skips_sent_and_rejected(pipeline):
    pipeline.found += [make_item(1, title="관련 도면 AI"), make_item(3, title="무관한 기사")]
    run(pipeline)
    pipeline.found.append(make_item(4, title="관련 새 기사"))
    result = run(pipeline, now=NOW + timedelta(days=1))
    assert pipeline.calls["triage"][-1] == ["관련 새 기사"]     # 보낸 기사·탈락 기사는 다시 판정하지 않는다
    assert [i["title"] for i in result["main"]] == ["관련 새 기사"]


def test_same_article_under_new_google_id_is_not_resent(pipeline):
    pipeline.found.append(make_item(1, title="관련 도면 AI"))
    run(pipeline)
    pipeline.found[:] = [make_item(9, title="관련 도면 AI")]    # Google이 같은 기사에 새 ID를 준 경우
    result = run(pipeline, now=NOW + timedelta(days=1))
    assert result["main"] == []
    assert result["candidates"] == 0


def test_slack_failure_keeps_history_unchanged(pipeline):
    pipeline.found += [make_item(1, title="관련 도면 AI"), make_item(3, title="무관한 기사")]
    result = run(pipeline, FakeNotifier(ok=False))
    assert result["notification"] is False
    state = pipeline.state()
    assert state.last_success is None
    assert not state.was_sent([make_item(1, title="관련 도면 AI").title_key])
    assert state.is_rejected(make_item(3, title="무관한 기사").title_key, NOW)   # 탈락 판정은 남긴다
    # 다음 실행에서 다시 보낸다
    result = run(pipeline, now=NOW + timedelta(hours=1))
    assert [i["title"] for i in result["main"]] == ["관련 도면 AI"]


def test_incomplete_search_does_not_advance_last_success(pipeline):
    pipeline.search_complete["value"] = False
    pipeline.found.append(make_item(1, title="관련 도면 AI"))
    result = run(pipeline)
    assert result["notification"] is True
    assert "Google News 검색 3/22회 실패" in result["warnings"]
    state = pipeline.state()
    assert state.last_success is None
    assert state.was_sent([make_item(1, title="관련 도면 AI").title_key])


def test_dry_run_sends_nothing_and_saves_nothing(pipeline):
    pipeline.found.append(make_item(1, title="관련 도면 AI"))
    notifier = FakeNotifier()
    result = run(pipeline, notifier, dry_run=True)
    assert notifier.sent == []
    assert result["slack_preview"]
    state = pipeline.state()
    assert state.last_success is None and state.sent == {} and state.judged == {}


def test_empty_day_still_posts(pipeline):
    notifier = FakeNotifier()
    result = run(pipeline, notifier)
    assert result["candidates"] == 0
    assert len(notifier.sent) == 1
    assert pipeline.state().last_success == NOW


def test_infra_overflow_is_capped_and_cached(pipeline, monkeypatch):
    monkeypatch.setenv("AEC_NEWS_MAX_INFRA", "2")
    pipeline.found += [make_item(i, title=f"인프라 기사 {i}", hour=i) for i in range(1, 5)]
    result = run(pipeline)
    assert [i["title"] for i in result["infra"]] == ["인프라 기사 4", "인프라 기사 3"]   # 최신순 2건
    state = pipeline.state()
    assert state.is_rejected(make_item(1, title="인프라 기사 1").title_key, NOW)


def test_infra_section_can_be_disabled(pipeline, monkeypatch):
    monkeypatch.setenv("AEC_NEWS_INCLUDE_INFRA", "0")
    pipeline.found.append(make_item(1, title="인프라 기사"))
    assert run(pipeline)["infra"] == []


def test_hours_option_sets_days_for_all_regions(pipeline):
    result = run(pipeline, dry_run=True, hours=30)
    assert result["days_by_region"] == {"KR": 2, "US": 2, "SG": 2}


def test_no_llm_uses_keyword_rules(pipeline):
    pipeline.found.append(make_item(1, title="현대건설, 생성형 AI로 도면 검토"))
    result = run(pipeline, dry_run=True, use_llm=False)
    assert pipeline.calls["triage"] == [] and pipeline.calls["summarize"] == 0
    assert [i["title"] for i in result["main"]] == ["현대건설, 생성형 AI로 도면 검토"]
    assert any("--no-llm" in w for w in result["warnings"])


def test_execute_workflow_skips_when_locked(monkeypatch):
    assert m._run_lock.acquire(blocking=False)
    try:
        assert m.execute_workflow(dry_run=True) is None
    finally:
        m._run_lock.release()


def test_regions_in_digest_follow_region_order(pipeline):
    pipeline.found += [make_item(1, title="관련 US", region=Region.US, outlet_key="reuters", outlet_name="Reuters"),
                       make_item(2, title="관련 KR")]
    result = run(pipeline, dry_run=True)
    text = m._preview_text(result["slack_preview"])
    assert text.index("🇰🇷 국내") < text.index("🇺🇸 미국")


def test_failed_thread_reply_items_are_resent_next_run(pipeline):
    pipeline.found += [make_item(i, title=f"관련 기사 {i}") for i in range(1, 70)]
    notifier = FakeNotifier(fail_indices={1})
    result = run(pipeline, notifier)
    messages = notifier.sent[0]
    assert len(messages) >= 2
    in_thread = {i.title for i in messages[1].items}
    assert in_thread
    assert any("스레드 답글" in w for w in result["warnings"])
    state = pipeline.state()
    assert not any(state.was_sent([make_item(1, title=t).title_key]) for t in in_thread)
    assert all(state.was_sent([i.title_key]) for i in messages[0].items)
    # 다음 실행에서 스레드에 있던 기사만 다시 보낸다
    result = run(pipeline, now=NOW + timedelta(days=1))
    assert {i["title"] for i in result["main"]} == in_thread


def test_unjudged_items_are_not_cached(pipeline, monkeypatch):
    def triage_with_gap(items, threshold):
        out = TriageOutcome()
        out.unjudged.extend(items)
        out.warnings.append("AI 판정 응답에서 빠진 기사 1건 — 다음 실행에서 다시 판정합니다")
        return out

    monkeypatch.setattr(m, "triage", triage_with_gap)
    pipeline.found.append(make_item(1, title="관련 도면 AI"))
    result = run(pipeline)
    assert result["main"] == []
    assert pipeline.state().judged == {}


def test_merged_alias_google_id_is_recorded(pipeline):
    pipeline.found += [make_item(1, title="관련 도면 AI"), make_item(2, title="관련 도면 AI")]   # 같은 기사, ID 2개
    run(pipeline)
    state = pipeline.state()
    assert "gid:CBMi0001" in state.sent and "gid:CBMi0002" in state.sent
