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

    def __init__(self, ok=True, fail_indices=(), upload_ok=True):
        self.ok = ok
        self.fail_indices = set(fail_indices)
        self.sent = []
        self.uploads = []
        self.events = []
        self.upload_ok = upload_ok
        self.last_ts = None

    def send(self, messages):
        self.sent.append(messages)
        self.events.append("send")
        if not self.ok:
            return []
        self.last_ts = "111.222"
        return [i for i in range(len(messages)) if i not in self.fail_indices]

    def upload_file(self, content, filename, title, thread_ts=None, comment=""):
        self.events.append("upload")
        self.uploads.append({"content": content, "filename": filename, "thread_ts": thread_ts, "comment": comment})
        return self.upload_ok


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """search/triage/resolve/summarize를 가짜로. triage 규칙: 제목에 '관련'이 있으면 main, '인프라'면 infra."""
    found: list = []
    calls = {"triage": [], "summarize": 0}
    search_complete = {"value": True}

    def fake_search(now, days_by_region, hours=None, client=None, competitor_days=None, web=False):
        calls["competitor_days"] = competitor_days
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

    def fake_resolve(items, cache, client=None, competitor_mode=False):
        for i in items:
            i.url = cache.setdefault(i.google_id, f"https://www.donga.com/news/{i.google_id}")
        return list(items), []

    def fake_summarize(items, recent=None, repeats=None, same_competitor=False):
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
    kw.setdefault("fetcher", FakeFetcher({}))   # 원문 받기는 네트워크를 타지 않게
    kw.setdefault("sleep", lambda sec: None)     # HTML 업로드 뒤 대기·게시 시각 대기를 건너뛴다
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
    assert set(result["days_by_region"].values()) == {2}
    assert set(result["days_by_region"]) == {r.value for r in Region}


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


# --- 경쟁사 ---

class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch_text(self, url):
        self.calls.append(url)
        return self.pages.get(url)


def comp_item(no, title, hits=("fobecon",), hour=1, themes=()):
    item = make_item(no, title=title, outlet_key=f"web:site{no}.kr", outlet_name=f"매체{no}", themes=themes, hour=hour)
    item.competitor_hits = list(hits)
    return item


def test_check_mentions_title_vs_body(monkeypatch):
    titled = comp_item(1, "포비콘, 적산 넘어 BIM까지")
    body_ok = comp_item(2, "아이디어정보기술, 이데아플랫폼 파트너스 데이 성료", hour=3)
    body_none = comp_item(3, "건설 업계 소식 모음", hour=2)
    cache = {body_ok.google_id: "https://a.kr/2", body_none.google_id: "https://a.kr/3"}
    fetcher = FakeFetcher({"https://a.kr/2": "본문 시작 … 페이컴스, 포비콘, 제호바 등 협력사가 발표했다 …",
                           "https://a.kr/3": "포비 관련 없는 본문"})

    def fake_resolve(items, cache_, client=None, competitor_mode=False):
        for i in items:
            i.url = cache_.get(i.google_id)
        return list(items), []

    monkeypatch.setattr(m, "resolve_urls", fake_resolve)
    verify, no_mention, pending = m.check_competitor_mentions([titled, body_ok, body_none], cache, None, fetcher, 15)
    assert [i.google_id for i in verify] == [titled.google_id, body_ok.google_id]
    assert titled.mention == "title" and body_ok.mention == "body"
    assert "포비콘" in body_ok.excerpt
    assert no_mention == [body_none] and pending == []


def test_check_mentions_respects_cap_and_fetch_failure(monkeypatch):
    items = [comp_item(i, f"단신 {i}", hour=i) for i in range(1, 5)]

    def fake_resolve(items_, cache_, client=None, competitor_mode=False):
        for i in items_:
            i.url = f"https://a.kr/{i.google_id}"
        return list(items_), []

    monkeypatch.setattr(m, "resolve_urls", fake_resolve)
    verify, no_mention, pending = m.check_competitor_mentions(items, {}, None, FakeFetcher({}), 2)
    assert verify == [] and no_mention == []
    assert {i.google_id for i in pending} == {i.google_id for i in items}   # 상한 밖 2 + 받기 실패 2


def test_drop_repeated_stories_by_majority():
    a, b, c = make_item(1, story=1), make_item(2, story=1), make_item(3, story=1)
    d = make_item(4, story=4)
    assert m.drop_repeated_stories([a, b, c, d], [a]) == []            # 3건 중 1건만 표시 → 유지
    assert m.drop_repeated_stories([a, b, c, d], [a, b]) == [a, b, c]  # 과반 → 묶음 전체
    assert m.drop_repeated_stories([a, b, c, d], [d]) == [d]


def test_competitor_pipeline_moves_items_to_competitor_section(pipeline, monkeypatch):
    def fake_verify(items, competitors, threshold):
        out = m.CompetitorOutcome()
        for i in items:
            if "인포시즈" in i.title:
                i.competitor, i.score = "infocz", 0.9
                out.matched.append(i)
            else:
                out.rejected.append(i)
        return out

    monkeypatch.setattr(m, "verify_competitors", fake_verify)
    monkeypatch.setattr(m, "COMPETITORS", m.COMPETITORS)
    hit = make_item(1, title="인포시즈, 서울대와 관련 도면 AI 개발")      # 주제 검색에도 걸린 기사
    hit.competitor_hits = ["infocz"]
    homonym = make_item(2, title="관련 없는 BHSN 학교 소식", themes=())    # 경쟁사 아님 + 주제 없음 → 탈락 캐시
    homonym.competitor_hits = ["bhsn"]
    topic = make_item(3, title="관련 건설 AI 기사")
    pipeline.found += [hit, homonym, topic]
    result = run(pipeline, fetcher=FakeFetcher({}))
    assert [i["title"] for i in result["competitors"]] == ["인포시즈, 서울대와 관련 도면 AI 개발"]
    assert result["competitors"][0]["competitor"] == "infocz"
    assert [i["title"] for i in result["main"]] == ["관련 건설 AI 기사"]       # 경쟁사 기사는 본문 섹션에 중복되지 않는다
    assert pipeline.calls["triage"][-1] == ["관련 건설 AI 기사"]
    state = pipeline.state()
    assert state.is_rejected(make_item(2, title="관련 없는 BHSN 학교 소식").title_key, NOW)
    assert pipeline.calls["competitor_days"] == 7


def test_repeated_story_is_skipped_and_recorded(pipeline, monkeypatch):
    def summarize_marking(items, recent=None, repeats=None, same_competitor=False):
        for n, i in enumerate(items, 1):
            i.summary_ko, i.story = "요약", n
            if recent and "관련 재탕" in i.title and repeats is not None:
                repeats.append(i)
        return None

    monkeypatch.setattr(m, "summarize", summarize_marking)
    pipeline.found.append(make_item(1, title="관련 원본 소식"))
    run(pipeline)
    assert pipeline.state().recent_titles(NOW)[0]["title"] == "관련 원본 소식"
    pipeline.found[:] = [make_item(2, title="관련 재탕 소식", outlet_key="mk", outlet_name="매일경제")]
    result = run(pipeline, now=NOW + timedelta(days=1))
    assert result["main"] == [] and result["repeats_skipped"] == 1
    state = pipeline.state()
    assert state.was_sent([make_item(2, title="관련 재탕 소식", outlet_key="mk").title_key])


# --- 리뷰 지적 회귀 테스트 (2026-11-13) ---

def test_dedupe_run_keeps_competitor_hits_from_competitor_copy():
    topic = make_item(1, title="쪼개 읽던 산업 도면, AI가 통째로 읽는다")          # 주제 검색 사본 (먼저 옴)
    comp = make_item(1, title="쪼개 읽던 산업 도면, AI가 통째로 읽는다", themes=())  # 경쟁사 검색 사본
    comp.competitor_hits = ["infocz"]
    out = m.dedupe_run([topic, comp])
    assert out == [topic] and topic.competitor_hits == ["infocz"]


def test_unchecked_topic_item_still_goes_to_topic_triage(pipeline, monkeypatch):
    # 본문 확인을 못 한(미룸) 기사라도 주제 검색으로 찾았으면 주제 판정은 받는다 (국내 검색 기간 2일 안에)
    def pending_all(items, cache, client, fetcher, cap):
        return [], [], list(items)

    monkeypatch.setattr(m, "check_competitor_mentions", pending_all)
    item = make_item(1, title="관련 건설 AI 기사")
    item.competitor_hits = ["fobecon"]
    pipeline.found.append(item)
    result = run(pipeline)
    assert pipeline.calls["triage"][-1] == ["관련 건설 AI 기사"]
    assert [i["title"] for i in result["main"]] == ["관련 건설 AI 기사"]


def test_competitor_rejected_topic_item_loses_excerpt(pipeline, monkeypatch):
    seen = {}

    def body_mention(items, cache, client, fetcher, cap):
        for i in items:
            i.mention, i.excerpt = "body", "… 포비콘 협력 …"
        return list(items), [], []

    def reject_all(items, competitors, threshold):
        out = m.CompetitorOutcome()
        out.rejected.extend(items)
        return out

    def triage_spy(items, threshold):
        seen["excerpts"] = [i.excerpt for i in items]
        return m.TriageOutcome()

    monkeypatch.setattr(m, "check_competitor_mentions", body_mention)
    monkeypatch.setattr(m, "verify_competitors", reject_all)
    monkeypatch.setattr(m, "triage", triage_spy)
    item = make_item(1, title="관련 건설 AI 기사")
    item.competitor_hits = ["fobecon"]
    pipeline.found.append(item)
    run(pipeline)
    assert seen["excerpts"] == [""]


def test_main_summary_sees_todays_competitor_titles(pipeline, monkeypatch):
    calls = []

    def verify_all(items, competitors, threshold):
        out = m.CompetitorOutcome()
        for i in items:
            i.competitor, i.score = "infocz", 0.9
            out.matched.append(i)
        return out

    def summarize_spy(items, recent=None, repeats=None, same_competitor=False):
        calls.append((same_competitor, [r["title"] for r in (recent or [])]))
        for n, i in enumerate(items, 1):
            i.story = n
            if not same_competitor and "동아 사본" in i.title and recent:
                repeats.append(i)
        return None

    monkeypatch.setattr(m, "verify_competitors", verify_all)
    monkeypatch.setattr(m, "summarize", summarize_spy)
    comp = make_item(1, title="인포시즈, 서울대와 관련 도면 AI", outlet_key="mt", outlet_name="머니투데이")
    comp.competitor_hits = ["infocz"]
    topic = make_item(2, title="관련 동아 사본 — 도면 AI")
    pipeline.found += [comp, topic]
    result = run(pipeline)
    assert calls[0][0] is True and calls[1][0] is False
    assert "인포시즈, 서울대와 관련 도면 AI" in calls[1][1]          # 주제 요약에 오늘 경쟁사 기사 제목이 들어간다
    assert result["main"] == [] and len(result["competitors"]) == 1
    assert result["repeats_skipped"] == 1


def test_competitor_cap_counts_stories_and_extra_copies_are_not_decoded(pipeline, monkeypatch):
    monkeypatch.setenv("AEC_NEWS_MAX_COMPETITOR", "2")
    decoded = []

    def verify_all(items, competitors, threshold):
        out = m.CompetitorOutcome()
        for i in items:
            i.competitor, i.score = "wisenut", 0.9
            out.matched.append(i)
        return out

    def summarize_story(items, recent=None, repeats=None, same_competitor=False):
        for i in items:
            i.story = 1 if "수주" in i.title else (2 if "실적" in i.title else 3)
        return None

    def resolve_spy(items, cache, client=None, competitor_mode=False):
        decoded.extend(i.google_id for i in items)
        return list(items), []

    monkeypatch.setattr(m, "verify_competitors", verify_all)
    monkeypatch.setattr(m, "summarize", summarize_story)
    monkeypatch.setattr(m, "resolve_urls", resolve_spy)
    items = []
    for n in range(1, 11):     # 같은 수주 소식 사본 10곳 (최신)
        it = make_item(n, title=f"와이즈넛 쇼핑엔티 수주 {n}", outlet_key=f"web:o{n}", outlet_name=f"매체{n}", hour=20)
        it.competitor_hits = ["wisenut"]
        items.append(it)
    for n, (title, hour) in enumerate([("와이즈넛 상반기 실적", 10), ("와이즈넛 자사주", 1)], 11):
        it = make_item(n, title=title, outlet_key=f"web:o{n}", outlet_name=f"매체{n}", hour=hour)
        it.competitor_hits = ["wisenut"]
        items.append(it)
    pipeline.found += items
    result = run(pipeline)
    titles = [i["title"] for i in result["competitors"]]
    assert len(titles) == 11                                  # 소식 2개 = 수주 사본 10 + 실적 1 (자사주는 다음 실행)
    assert "와이즈넛 자사주" not in titles
    assert sorted(decoded) == ["CBMi0001", "CBMi0011"]          # 묶음 대표만 복원 (수주·실적), 사본은 Google 링크
    state = pipeline.state()
    assert all(state.was_sent([make_item(n, title=f"와이즈넛 쇼핑엔티 수주 {n}", outlet_key=f"web:o{n}").title_key])
               for n in range(1, 11))                         # 복원 안 한 사본도 '보냄'으로 기록
    assert not state.was_sent([make_item(12, title="와이즈넛 자사주", outlet_key="web:o12").title_key])


def test_body_checks_prefer_korean_candidates(monkeypatch):
    checked = []

    def resolve_spy(items, cache, client=None, competitor_mode=False):
        checked.extend(i.google_id for i in items)
        return [], []

    monkeypatch.setattr(m, "resolve_urls", resolve_spy)
    glob = [comp_item(i, f"Trimble news {i}", hits=("documentcrunch",), hour=20) for i in range(1, 4)]
    for g in glob:
        g.region = Region.GLOBAL
    kr = [comp_item(i, f"단신 {i}", hits=("doaz",), hour=1) for i in range(4, 6)]
    m.check_competitor_mentions(glob + kr, {}, None, FakeFetcher({}), 3)
    assert checked == ["CBMi0004", "CBMi0005", "CBMi0001"]     # 국내 2건 먼저, 남은 1자리는 최신 해외



# --- HTML 정리본 ---

def test_html_report_posted_first_then_digest(pipeline):
    pipeline.found.append(make_item(1, title="관련 도면 AI <특집> & 분석"))
    notifier = FakeNotifier()
    slept = []
    result = run(pipeline, notifier, sleep=slept.append)
    assert notifier.events == ["upload", "send"]                 # HTML 파일이 먼저, 브리핑 메시지가 바로 아래
    assert result["html_uploaded"] is True and slept == [2]
    up = notifier.uploads[0]
    assert up["thread_ts"] is None and up["filename"] == "aec-news-20261113.html"   # 스레드가 아니라 채널에
    assert "바로 아래 메시지" in up["comment"]
    page = up["content"].decode("utf-8")
    assert "관련 도면 AI &lt;특집&gt; &amp; 분석" in page        # 제목 이스케이프
    assert "<script" not in page                                # 외부·인라인 스크립트 없음
    header = notifier.sent[0][0].blocks[2]["elements"][0]["text"]
    assert "HTML 정리본은 바로 위 파일" in header


def test_html_upload_failure_does_not_affect_history(pipeline):
    pipeline.found.append(make_item(1, title="관련 도면 AI"))
    result = run(pipeline, FakeNotifier(upload_ok=False))
    assert result["notification"] is True and result["html_uploaded"] is False
    assert pipeline.state().was_sent([make_item(1, title="관련 도면 AI").title_key])


def test_digest_still_sent_when_html_upload_fails(pipeline):
    pipeline.found.append(make_item(1, title="관련 도면 AI"))
    notifier = FakeNotifier(upload_ok=False)
    slept = []
    result = run(pipeline, notifier, sleep=slept.append)
    assert notifier.events == ["upload", "send"] and result["notification"] is True
    assert slept == []                                          # 업로드가 실패하면 기다리지 않는다


def test_html_can_be_disabled(pipeline, monkeypatch):
    monkeypatch.setenv("AEC_NEWS_HTML", "0")
    notifier = FakeNotifier()
    result = run(pipeline, notifier)
    assert notifier.uploads == [] and "html_uploaded" not in result
    assert "HTML 정리본" not in m._preview_text([{"blocks": b} for b in [notifier.sent[0][0].blocks]])


def test_web_section_capped_by_score_and_checked_last(pipeline, monkeypatch):
    monkeypatch.setenv("AEC_NEWS_MAX_WEB", "2")
    contexts = []

    def summarize_spy(items, recent=None, repeats=None, same_competitor=False):
        contexts.append(([i.region for i in items], [r["title"] for r in (recent or [])]))
        for n, i in enumerate(items, 1):
            i.story = n
        return None

    def triage_scored(items, threshold):
        out = TriageOutcome()
        for n, i in enumerate(items):
            i.score, i.theme = 0.9 - n * 0.05, Theme.AEC_AI
            out.main.append(i)
        return out

    monkeypatch.setattr(m, "summarize", summarize_spy)
    monkeypatch.setattr(m, "triage", triage_scored)
    major = make_item(1, title="관련 주요 언론 기사")
    webs = [make_item(10 + n, title=f"관련 기타 매체 {n}", region=Region.WEB_KR, outlet_key=f"web:w{n}",
                      outlet_name=f"매체{n}") for n in range(4)]
    pipeline.found += [major, *webs]
    result = run(pipeline)
    web_titles = [i["title"] for i in result["main"] if i["region"] == "WEB_KR"]
    assert sorted(web_titles) == ["관련 기타 매체 0", "관련 기타 매체 1"]  # 점수 상위 2개 소식
    assert contexts[-1][0] == [Region.WEB_KR] * 4                       # 기타 매체는 마지막에 따로 요약
    assert "관련 주요 언론 기사" in contexts[-1][1]                        # 앞 섹션 제목과 비교
    assert not pipeline.state().was_sent([webs[3].title_key])           # 잘린 소식은 다음 실행에서


def test_web_and_infra_items_are_not_decoded(pipeline, monkeypatch):
    decoded = []

    def resolve_spy(items, cache, client=None, competitor_mode=False):
        decoded.extend(i.title for i in items)
        return list(items), []

    monkeypatch.setattr(m, "resolve_urls", resolve_spy)
    pipeline.found += [make_item(1, title="관련 주요 언론"),
                       make_item(2, title="관련 기타 매체", region=Region.WEB_KR, outlet_key="web:x", outlet_name="X"),
                       make_item(3, title="인프라 데이터센터")]
    result = run(pipeline)
    assert decoded == ["관련 주요 언론"]
    assert {i["title"] for i in result["main"]} == {"관련 주요 언론", "관련 기타 매체"}
    assert [i["title"] for i in result["infra"]] == ["인프라 데이터센터"]


def test_scheduled_post_time_and_wait(pipeline):
    from src.aec_news.config import KST, get_config
    cfg = get_config()
    start = datetime(2026, 11, 13, 22, 30, tzinfo=timezone.utc)         # 11/14 07:30 KST
    post = m.scheduled_post_time(start, cfg)
    assert post == datetime(2026, 11, 14, 8, 30, tzinfo=KST)
    late = datetime(2026, 11, 13, 23, 40, tzinfo=timezone.utc)          # 08:40 — 이미 지남 → 바로
    assert m.scheduled_post_time(late, cfg) == datetime(2026, 11, 14, 8, 30, tzinfo=KST)

    slept = []
    future = datetime.now(timezone.utc) + timedelta(minutes=12)
    notifier = FakeNotifier()
    run(pipeline, notifier, post_at=future, sleep=slept.append)
    assert len(slept) == 2 and 700 <= slept[0] <= 721 and slept[1] == 2  # 게시 시각까지 기다린 뒤 (HTML 업로드 후 2초)
    assert len(notifier.sent) == 1                                      # 보낸다
    slept.clear()
    run(pipeline, FakeNotifier(), post_at=datetime.now(timezone.utc) - timedelta(minutes=5), sleep=slept.append)
    assert slept == [2]                                                 # 늦으면 바로 보낸다 (HTML 업로드 후 2초만)


def test_scheduler_starts_collection_lead_minutes_before_post(monkeypatch):
    added = {}

    class FakeScheduler:
        def __init__(self, timezone):
            pass

        def add_job(self, func, trigger, **kw):
            added["func"], added["trigger"] = func, trigger

        def start(self):
            added["started"] = True

    import apscheduler.schedulers.blocking as blocking
    monkeypatch.setattr(blocking, "BlockingScheduler", FakeScheduler)
    m.main([])
    assert added["func"] is m.execute_scheduled and added["started"]
    fields = {f.name: str(f) for f in added["trigger"].fields}
    assert fields["hour"] == "7" and fields["minute"] == "30"           # 07:30 수집 시작 → 08:30 게시
