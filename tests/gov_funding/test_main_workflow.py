"""run_workflow 파이프라인 테스트 (크롤러/Bedrock/Slack/S3 Vectors 전부 가짜)"""
import importlib
import logging
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.gov_funding.analyzers import BedrockUnavailableError
from src.gov_funding.storage import Announcement, Source

main_module = importlib.import_module("src.gov_funding.main")

TODAY = date(2026, 10, 20)


def make_ann(source: Source, ann_id: str, title: str, d_day=None, deadline=None) -> Announcement:
    return Announcement(
        id=ann_id,
        source=source,
        title=title,
        d_day=d_day,
        deadline=deadline,
        url=f"https://example.com/{source.value}/{ann_id}",
    )


def meta(title: str, score: float = 0.9, d_day: int = -1, deadline: str = "", summary: str = "저장된 요약",
         source: str = "kstartup", crawled_at: str = "2026-10-01T00:00:00") -> dict:
    return {
        "source": source,
        "title": title,
        "relevance_score": score,
        "d_day": d_day,
        "deadline": deadline,
        "summary": summary,
        "url": "https://example.com/stored",
        "crawled_at": crawled_at,
    }


class FakeCrawler:
    def __init__(self, items=None, error=None, close_error=None):
        self.items = items or []
        self.error = error
        self.close_error = close_error
        self.closed = False

    async def crawl(self, max_items=50):
        if self.error:
            raise self.error
        return list(self.items)

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeVectorStorage:
    def __init__(self, index=None, error=None, upsert_error=None):
        self.index = index or {}
        self.error = error
        self.upsert_error = upsert_error
        self.upserted = None

    def ensure_index(self):
        pass

    def get_existing_index(self):
        if self.error:
            raise self.error
        return dict(self.index)

    def upsert_announcements(self, announcements):
        if self.upsert_error:
            raise self.upsert_error
        self.upserted = list(announcements)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """파이프라인 의존성 교체. 필터는 통과시키고 Bedrock은 받은 공고를 그대로 통과시킨다."""
    state = {"vs": FakeVectorStorage(), "crawlers": []}
    monkeypatch.setenv("GOV_FUNDING_STATE_DIR", str(tmp_path))

    monkeypatch.setattr(main_module, "S3VectorStorage", lambda: state["vs"])
    monkeypatch.setattr(main_module, "create_crawlers", lambda: state["crawlers"])
    monkeypatch.setattr(main_module, "deadline_filter", lambda anns: anns)
    monkeypatch.setattr(main_module, "keyword_filter", lambda anns: anns)
    monkeypatch.setattr(main_module, "today_kst", lambda: TODAY)
    monkeypatch.setattr(main_module, "now_kst", lambda: datetime(2026, 10, 20, 9, 0))

    async def fake_bedrock(anns, threshold=0.7, crawlers=None):
        for a in anns:
            a.relevance_score = 0.9
            a.summary = "새 요약"
        return list(anns)

    state["bedrock"] = AsyncMock(side_effect=fake_bedrock)
    state["slack"] = AsyncMock(return_value=True)
    state["gmail"] = AsyncMock(return_value=None)
    monkeypatch.setattr(main_module, "filter_with_bedrock", state["bedrock"])
    monkeypatch.setattr(main_module, "send_slack_notification", state["slack"])
    monkeypatch.setattr(main_module, "send_gmail_notification", state["gmail"])
    return state


def slack_payload(state):
    return state["slack"].call_args.args[0]


def slack_ops_notes(state):
    return state["slack"].call_args.kwargs.get("ops_notes")


@pytest.mark.asyncio
async def test_crawler_exception_does_not_abort_run(env):
    good = FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 스타트업 지원사업")])
    bad = FakeCrawler(error=RuntimeError("chromium crashed"))
    env["crawlers"] = [("kstartup", bad, 300), ("bizinfo", good, 300)]

    result = await main_module.run_workflow()

    assert result["crawled"] == {"kstartup": 0, "bizinfo": 1}
    assert any("kstartup" in e for e in result["errors"])
    assert [a.id for a in slack_payload(env).new_announcements] == ["PBLN_1"]
    assert any("kstartup" in n for n in slack_ops_notes(env))
    assert bad.closed and good.closed


@pytest.mark.asyncio
async def test_close_error_does_not_skip_other_closes(env):
    first = FakeCrawler([make_ann(Source.NIPA, "1", "SW 개발 지원")], close_error=RuntimeError("boom"))
    second = FakeCrawler([make_ann(Source.NIA, "2", "데이터 바우처")])
    env["crawlers"] = [("nipa", first, 50), ("nia", second, 50)]

    result = await main_module.run_workflow()

    assert first.closed and second.closed
    assert result["new_announcements"] == 2


@pytest.mark.asyncio
async def test_zero_item_source_is_reported(env):
    env["crawlers"] = [
        ("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300),
        ("jointips", FakeCrawler([]), 50),
    ]

    result = await main_module.run_workflow()

    assert "jointips: 0건" in result["warnings"]
    assert "jointips: 0건" in slack_ops_notes(env)


@pytest.mark.asyncio
async def test_stored_items_skip_bedrock_and_reuse_summary(env):
    stored = make_ann(Source.KSTARTUP, "100", "기존 AI 공고", d_day=20)
    fresh = make_ann(Source.KSTARTUP, "200", "새로운 AI 공고", d_day=20)
    env["vs"].index = {"kstartup_100": meta("기존 AI 공고", d_day=21)}
    env["crawlers"] = [("kstartup", FakeCrawler([stored, fresh]), 300)]

    result = await main_module.run_workflow()

    sent_to_bedrock = env["bedrock"].call_args.args[0]
    assert [a.id for a in sent_to_bedrock] == ["200"]
    assert [a.id for a in slack_payload(env).new_announcements] == ["200"]
    assert {a.id for a in env["vs"].upserted} == {"100", "200"}
    restored = next(a for a in env["vs"].upserted if a.id == "100")
    assert restored.summary == "저장된 요약"
    assert result["filtered"] == 2


@pytest.mark.asyncio
async def test_same_program_under_other_source_is_not_new(env):
    """K-Startup으로 이미 알린 사업이 기업마당에 새 ID로 올라와도 신규로 다시 알리지 않는다"""
    env["vs"].index = {"kstartup_100": meta("2026년 AI 바우처 지원사업 모집 공고")}
    dup = make_ann(Source.BIZINFO, "PBLN_9", "2026년 AI 바우처 지원사업 모집 공고")
    env["crawlers"] = [("bizinfo", FakeCrawler([dup]), 300)]

    result = await main_module.run_workflow()

    env["bedrock"].assert_not_awaited()
    assert result["new_announcements"] == 0
    assert [a.id for a in env["vs"].upserted] == ["PBLN_9"]


@pytest.mark.asyncio
async def test_stored_low_score_is_dropped(env):
    env["vs"].index = {"nia_1": meta("저점수 공고", score=0.5)}
    env["crawlers"] = [("nia", FakeCrawler([make_ann(Source.NIA, "1", "저점수 공고")]), 50)]

    result = await main_module.run_workflow()

    assert result["filtered"] == 0
    env["bedrock"].assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_index_failure_sends_no_announcements(env):
    env["vs"].error = RuntimeError("AccessDenied")
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    result = await main_module.run_workflow()

    env["bedrock"].assert_not_awaited()
    assert not slack_payload(env).has_content
    assert any("벡터 저장소 조회 실패" in n for n in slack_ops_notes(env))
    assert env["vs"].upserted is None
    assert result["new_announcements"] == 0


@pytest.mark.asyncio
async def test_bedrock_unavailable_keeps_reminders_and_skips_new(env):
    stored = make_ann(Source.NIPA, "1", "저장된 SW 공고", d_day=7)
    fresh = make_ann(Source.NIPA, "2", "새 SW 공고", d_day=30)
    env["vs"].index = {"nipa_1": meta("저장된 SW 공고", d_day=8, source="nipa")}
    env["crawlers"] = [("nipa", FakeCrawler([stored, fresh]), 50)]
    env["bedrock"].side_effect = BedrockUnavailableError("Bedrock 분석 실패 3/3건")

    result = await main_module.run_workflow()

    payload = slack_payload(env)
    assert payload.new_announcements == []
    assert [a.id for a in payload.deadline_soon] == ["1"]
    assert "Bedrock 분석 실패 3/3건" in result["errors"]
    assert [a.id for a in env["vs"].upserted] == ["1"]


@pytest.mark.asyncio
async def test_slack_failure_skips_upsert(env):
    env["slack"].return_value = False
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    result = await main_module.run_workflow()

    assert env["vs"].upserted is None
    assert "Slack 알림 실패" in result["errors"]


@pytest.mark.asyncio
async def test_upsert_error_is_recorded(env):
    env["vs"].upsert_error = RuntimeError("throttled")
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    result = await main_module.run_workflow()

    assert any("Vector storage error" in e for e in result["errors"])
    assert result["notifications"]["slack"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("prev,today,expected", [
    (8, 7, True),    # D-7 마일스톤 통과
    (7, 6, False),   # 이미 D-7 알림함
    (9, 6, True),    # 하루 건너뛰어도 알림
    (4, 3, True),    # D-3 마일스톤 통과
    (-1, 5, True),   # 이전엔 마감일 없음
    (20, 10, False), # 아직 마일스톤 전
])
async def test_deadline_milestones(env, prev, today, expected):
    ann = make_ann(Source.MSS, "1", "중기부 AI 공고", d_day=today)
    env["vs"].index = {"mss_1": meta("중기부 AI 공고", d_day=prev, source="mss")}
    env["crawlers"] = [("mss", FakeCrawler([ann]), 50)]

    result = await main_module.run_workflow()

    assert result["deadline_soon"] == (1 if expected else 0)


@pytest.mark.asyncio
async def test_reminder_for_stored_item_not_crawled_today(env):
    """수집 범위 밖으로 밀려난 저장 공고도 저장된 마감일로 리마인더를 보낸다"""
    env["vs"].index = {
        "bizinfo_PBLN_5": meta("기업마당 AI 공고", d_day=20, deadline="2026-10-27T23:59:59", source="bizinfo"),
        "bizinfo_PBLN_6": meta("멀리 남은 공고", d_day=40, deadline="2026-11-30T23:59:59", source="bizinfo"),
        "bizinfo_PBLN_7": meta("마감 지난 공고", d_day=3, deadline="2026-10-01T23:59:59", source="bizinfo"),
    }
    env["crawlers"] = [("kstartup", FakeCrawler([make_ann(Source.KSTARTUP, "1", "다른 공고")]), 300)]

    result = await main_module.run_workflow()

    reminders = slack_payload(env).deadline_soon
    assert [(a.id, a.d_day) for a in reminders] == [("PBLN_5", 7)]
    assert result["deadline_soon"] == 1
    # 리마인더를 보낸 공고는 D-day를 갱신 저장해 다음 날 중복 알림하지 않는다
    upserted = {a.id: a for a in env["vs"].upserted}
    assert upserted["PBLN_5"].d_day == 7


@pytest.mark.asyncio
async def test_same_program_reminded_once(env):
    env["vs"].index = {
        "kstartup_1": meta("AI 데이터 바우처 공고", d_day=8),
        "bizinfo_PBLN_1": meta("AI 데이터 바우처 공고", d_day=8, deadline="2026-10-27T23:59:59", source="bizinfo"),
    }
    env["crawlers"] = [("kstartup", FakeCrawler([make_ann(Source.KSTARTUP, "1", "AI 데이터 바우처 공고", d_day=7)]), 300)]

    result = await main_module.run_workflow()

    assert result["deadline_soon"] == 1


@pytest.mark.asyncio
async def test_force_resend_sends_all_relevant(env):
    env["vs"].index = {"kstartup_1": meta("기존 공고", d_day=8)}
    env["crawlers"] = [("kstartup", FakeCrawler([
        make_ann(Source.KSTARTUP, "1", "기존 공고", d_day=7),
        make_ann(Source.KSTARTUP, "2", "새 공고"),
    ]), 300)]

    result = await main_module.run_workflow(force_resend=True)

    assert {a.id for a in slack_payload(env).new_announcements} == {"1", "2"}
    assert result["deadline_soon"] == 0


@pytest.mark.asyncio
async def test_nothing_to_send_and_no_errors_posts_nothing(env):
    env["vs"].index = {"kstartup_1": meta("기존 공고", d_day=30)}
    env["crawlers"] = [("kstartup", FakeCrawler([make_ann(Source.KSTARTUP, "1", "기존 공고", d_day=29)]), 300)]

    result = await main_module.run_workflow()

    env["slack"].assert_not_awaited()
    assert result["new_announcements"] == 0
    assert [a.id for a in env["vs"].upserted] == ["1"]


@pytest.mark.asyncio
async def test_gmail_not_configured_is_skipped(env):
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    result = await main_module.run_workflow()

    assert result["notifications"]["gmail"] == "skipped"
    assert result["errors"] == []


class TestMain:
    @pytest.fixture
    def scheduler(self, monkeypatch):
        sched = MagicMock()
        monkeypatch.setattr(main_module, "BlockingScheduler", lambda **kwargs: sched)
        return sched

    def test_startup_run_is_not_forced_by_default(self, monkeypatch, scheduler):
        monkeypatch.delenv("GOV_FUNDING_RUN_ON_START", raising=False)
        monkeypatch.delenv("GOV_FUNDING_FORCE_RESEND_ON_START", raising=False)
        execute = MagicMock()
        monkeypatch.setattr(main_module, "execute_workflow", execute)

        main_module.main([])

        execute.assert_called_once_with(force_resend=False)
        kwargs = scheduler.add_job.call_args.kwargs
        assert kwargs["misfire_grace_time"] == 3600
        assert kwargs["coalesce"] is True
        scheduler.start.assert_called_once()

    def test_startup_run_can_be_disabled(self, monkeypatch, scheduler):
        monkeypatch.setenv("GOV_FUNDING_RUN_ON_START", "0")
        execute = MagicMock()
        monkeypatch.setattr(main_module, "execute_workflow", execute)

        main_module.main([])

        execute.assert_not_called()

    def test_force_resend_on_start_is_opt_in(self, monkeypatch, scheduler):
        monkeypatch.setenv("GOV_FUNDING_FORCE_RESEND_ON_START", "1")
        monkeypatch.delenv("GOV_FUNDING_RUN_ON_START", raising=False)
        execute = MagicMock()
        monkeypatch.setattr(main_module, "execute_workflow", execute)

        main_module.main([])

        execute.assert_called_once_with(force_resend=True)


def test_execute_workflow_reports_crash(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("unexpected")

    slack = AsyncMock(return_value=True)
    monkeypatch.setattr(main_module, "run_workflow", boom)
    monkeypatch.setattr(main_module, "send_slack_notification", slack)

    main_module.execute_workflow()

    assert "워크플로우 오류: unexpected" in slack.call_args.kwargs["ops_notes"][0]


def test_gov_funding_logger_prints_once():
    """'gov-funding-monitor' 로그가 핸들러 하나로만 출력되는지 (루트 전파만 사용)"""
    log = logging.getLogger("gov-funding-monitor")
    assert log.handlers == []
    assert log.propagate is True


@pytest.mark.asyncio
async def test_title_match_ignores_closed_or_stale_entries(env):
    """같은 제목이라도 저장된 공고가 마감됐거나 오래전에 본 것이면 새 공고로 본다 (매년 같은 제목의 공고)"""
    title = "2026년「디지털콘텐츠 융합기업 글로벌 마켓 참가사업」지원기업 모집 공고"
    env["vs"].index = {
        "nipa_16577": meta(title, deadline="2026-04-15T23:59:59", crawled_at="2026-04-15T00:00:00", source="nipa"),
    }
    env["crawlers"] = [("nipa", FakeCrawler([make_ann(Source.NIPA, "16897", title, d_day=20)]), 50)]

    result = await main_module.run_workflow()

    assert [a.id for a in env["bedrock"].call_args.args[0]] == ["16897"]
    assert result["new_announcements"] == 1


@pytest.mark.asyncio
async def test_rejected_item_is_not_reanalyzed_next_run(env):
    async def reject_all(anns, threshold=0.7, crawlers=None):
        for a in anns:
            a.relevance_score = 0.4
        return []

    env["bedrock"].side_effect = reject_all
    crawler = FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")])
    env["crawlers"] = [("bizinfo", crawler, 300)]

    await main_module.run_workflow()
    assert env["bedrock"].await_count == 1

    await main_module.run_workflow()
    assert env["bedrock"].await_count == 1  # 두 번째 실행은 캐시로 생략


@pytest.mark.asyncio
async def test_failed_analysis_is_retried_next_run(env):
    """분석 실패(점수 없음) 공고는 캐시에 남기지 않아 다음 실행에서 다시 분석한다"""
    async def fail_all(anns, threshold=0.7, crawlers=None):
        return []

    env["bedrock"].side_effect = fail_all
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    await main_module.run_workflow()
    await main_module.run_workflow()

    assert env["bedrock"].await_count == 2


@pytest.mark.asyncio
async def test_bedrock_cap_defers_oldest(env, monkeypatch):
    monkeypatch.setattr(main_module, "MAX_BEDROCK_CALLS_PER_RUN", 2)
    items = []
    for i, day in enumerate([1, 5, 3]):
        ann = make_ann(Source.BIZINFO, f"PBLN_{i}", f"AI 지원 {i}")
        ann.posted_date = datetime(2026, 10, day)
        items.append(ann)
    env["crawlers"] = [("bizinfo", FakeCrawler(items), 300)]

    result = await main_module.run_workflow()

    assert [a.id for a in env["bedrock"].call_args.args[0]] == ["PBLN_1", "PBLN_2"]
    assert any("1건 다음 실행으로 연기" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_dry_run_has_no_side_effects(env, tmp_path):
    env["vs"].index = {"nipa_1": meta("저장된 SW 공고", d_day=8, source="nipa")}
    env["crawlers"] = [("nipa", FakeCrawler([
        make_ann(Source.NIPA, "1", "저장된 SW 공고", d_day=7),
        make_ann(Source.NIPA, "2", "새 SW 공고", d_day=30),
    ]), 50)]

    result = await main_module.run_workflow(dry_run=True)

    env["slack"].assert_not_awaited()
    env["gmail"].assert_not_awaited()
    assert env["vs"].upserted is None
    assert not (tmp_path / "rejected.json").exists()
    assert [p["title"] for p in result["preview"]["new"]] == ["새 SW 공고"]
    assert [p["title"] for p in result["preview"]["deadline_soon"]] == ["저장된 SW 공고"]


@pytest.mark.asyncio
async def test_dry_run_vector_failure_does_not_post(env):
    env["vs"].error = RuntimeError("AccessDenied")
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    result = await main_module.run_workflow(dry_run=True)

    env["slack"].assert_not_awaited()
    assert result["errors"]


@pytest.mark.asyncio
async def test_seed_stores_without_notifying(env):
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    result = await main_module.run_workflow(seed=True)

    env["slack"].assert_not_awaited()
    env["gmail"].assert_not_awaited()
    assert [a.id for a in env["vs"].upserted] == ["PBLN_1"]
    assert result["new_announcements"] == 1


class TestCli:
    @pytest.mark.parametrize("argv,expected", [
        (["--dry-run"], {"dry_run": True, "seed": False}),
        (["--seed"], {"dry_run": False, "seed": True}),
        (["--once"], {"dry_run": False, "seed": False}),
    ])
    def test_one_shot_modes_skip_scheduler(self, monkeypatch, argv, expected):
        monkeypatch.setenv("S3_BUCKET", "test-bucket")
        sched = MagicMock()
        monkeypatch.setattr(main_module, "BlockingScheduler", lambda **kwargs: sched)
        execute = MagicMock(return_value={"ok": True})
        monkeypatch.setattr(main_module, "execute_workflow", execute)

        main_module.main(argv)

        execute.assert_called_once_with(**expected)
        sched.start.assert_not_called()


@pytest.mark.asyncio
async def test_stored_deadline_restored_for_sources_without_list_deadline(env):
    """MSIT/MOTIE/NIA처럼 목록에 마감일이 없는 공고는 저장된(첨부에서 추출한) 마감일로 리마인더를 보낸다"""
    env["vs"].index = {"msit_1": meta("과기부 AI 공고", d_day=8, deadline="2026-10-27T23:59:59", source="msit")}
    env["crawlers"] = [("msit", FakeCrawler([make_ann(Source.MSIT, "1", "과기부 AI 공고")]), 50)]

    result = await main_module.run_workflow()

    assert result["deadline_soon"] == 1
    upserted = env["vs"].upserted[0]
    assert upserted.deadline == datetime(2026, 10, 27, 23, 59, 59)
    assert upserted.d_day == 7


@pytest.mark.asyncio
async def test_closed_stored_item_is_dropped_by_deadline_filter(env, monkeypatch):
    from src.gov_funding.analyzers.relevance_filter import deadline_filter
    monkeypatch.setattr(main_module, "deadline_filter", deadline_filter)
    monkeypatch.setattr("src.gov_funding.analyzers.relevance_filter.now_kst", lambda: datetime(2026, 10, 20, 9, 0))
    env["vs"].index = {"msit_1": meta("마감된 공고", deadline="2026-10-10T23:59:59", source="msit")}
    env["crawlers"] = [("msit", FakeCrawler([make_ann(Source.MSIT, "1", "마감된 공고")]), 50)]

    result = await main_module.run_workflow()

    assert result["filtered"] == 0


@pytest.mark.asyncio
async def test_old_key_twin_does_not_send_duplicate_reminder(env):
    """ID 체계가 바뀐 공고(옛 키)는 같은 사업이 오늘 새 키로 수집되면 리마인더 후보에서 빠진다"""
    title = "기업마당 AI 바우처 공고"
    env["vs"].index = {
        "bizinfo_8a8fd70ea578": meta(title, d_day=20, deadline="2026-10-27T23:59:59", source="bizinfo"),
        "bizinfo_PBLN_1": meta(title, d_day=8, deadline="2026-10-27T23:59:59", source="bizinfo"),
    }
    ann = make_ann(Source.BIZINFO, "PBLN_1", title, d_day=7, deadline=datetime(2026, 10, 27, 23, 59, 59))
    env["crawlers"] = [("bizinfo", FakeCrawler([ann]), 300)]

    result = await main_module.run_workflow()

    assert [a.id for a in slack_payload(env).deadline_soon] == ["PBLN_1"]
    assert result["deadline_soon"] == 1


@pytest.mark.asyncio
async def test_stale_candidates_are_all_upserted(env):
    """리마인더로 알리지 않은 수집 범위 밖 후보도 D-day를 갱신해 다음 날 늦은 중복 리마인더를 막는다"""
    env["vs"].index = {
        "kstartup_1": meta("AI 데이터 바우처", d_day=8, deadline="2026-10-27T23:59:59"),
        "bizinfo_PBLN_1": meta("AI 데이터 바우처 공고", d_day=8, deadline="2026-10-27T23:59:59", source="bizinfo"),
    }
    env["crawlers"] = [("nipa", FakeCrawler([make_ann(Source.NIPA, "9", "다른 SW 공고")]), 50)]

    result = await main_module.run_workflow()

    assert result["deadline_soon"] == 1
    upserted = {f"{a.source.value}_{a.id}": a.d_day for a in env["vs"].upserted}
    assert upserted["kstartup_1"] == 7
    assert upserted["bizinfo_PBLN_1"] == 7


@pytest.mark.asyncio
async def test_title_matched_item_reminded_with_twin_prev_d_day(env):
    env["vs"].index = {
        "kstartup_1": meta("AI 실증 지원 공고", d_day=9, deadline="2026-10-27T23:59:59",
                           crawled_at="2026-10-18T00:00:00"),
    }
    twin = make_ann(Source.BIZINFO, "PBLN_7", "AI 실증 지원 공고")
    env["crawlers"] = [("bizinfo", FakeCrawler([twin]), 300)]

    result = await main_module.run_workflow()

    assert result["new_announcements"] == 0
    assert [a.id for a in slack_payload(env).deadline_soon] == ["PBLN_7"]
    assert slack_payload(env).deadline_soon[0].d_day == 7


@pytest.mark.asyncio
async def test_upsert_failure_does_not_renotify_next_run(env):
    from src.gov_funding.storage.vector_storage import VectorUpsertError

    env["vs"].upsert_error = VectorUpsertError(["bizinfo_PBLN_1"])
    env["crawlers"] = [("bizinfo", FakeCrawler([make_ann(Source.BIZINFO, "PBLN_1", "AI 지원")]), 300)]

    first = await main_module.run_workflow()
    second = await main_module.run_workflow()

    assert first["new_announcements"] == 1
    assert second["new_announcements"] == 0
    assert env["bedrock"].await_count == 1


@pytest.mark.asyncio
async def test_dead_source_reported_on_quiet_day(env):
    env["vs"].index = {"kstartup_1": meta("기존 공고", d_day=30)}
    env["crawlers"] = [
        ("kstartup", FakeCrawler([make_ann(Source.KSTARTUP, "1", "기존 공고", d_day=29)]), 300),
        ("jointips", FakeCrawler([]), 50),
    ]

    await main_module.run_workflow()

    env["slack"].assert_awaited_once()
    assert not slack_payload(env).has_content
    assert slack_ops_notes(env) == ["jointips: 0건"]


def test_ops_notes_are_truncated():
    notes = main_module.ops_notes({"errors": ["x" * 500], "warnings": ["iitp: 0건"]})
    assert len(notes[0]) == main_module.MAX_OPS_NOTE_CHARS + 1
    assert notes[1] == "iitp: 0건"


def test_dry_run_crash_does_not_post(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("unexpected")

    slack = AsyncMock(return_value=True)
    monkeypatch.setattr(main_module, "run_workflow", boom)
    monkeypatch.setattr(main_module, "send_slack_notification", slack)

    assert main_module.execute_workflow(dry_run=True) is None
    assert main_module.execute_workflow(seed=True) is None
    slack.assert_not_awaited()


def test_cli_requires_env(monkeypatch):
    monkeypatch.delenv("S3_BUCKET", raising=False)
    with pytest.raises(SystemExit) as exc:
        main_module.main(["--dry-run"])
    assert exc.value.code == 2


def test_cli_exits_nonzero_when_run_fails(monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setattr(main_module, "execute_workflow", MagicMock(return_value=None))
    with pytest.raises(SystemExit) as exc:
        main_module.main(["--once"])
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_similar_title_from_other_source_is_not_new(env):
    """제목 표기만 다른 같은 사업(실제 사례: K-Startup vs 기업마당)은 다음 날 다시 신규로 알리지 않는다"""
    env["vs"].index = {
        "kstartup_179600": meta("2026년 서초창업스테이션 프리-오픈이노베이션", crawled_at="2026-10-19T00:00:00"),
    }
    dup = make_ann(Source.BIZINFO, "PBLN_000000000127266", "2026년 서초창업스테이션 프리 오픈 이노베이션 참여 기업 모집 공고")
    env["crawlers"] = [("bizinfo", FakeCrawler([dup]), 300)]

    result = await main_module.run_workflow()

    env["bedrock"].assert_not_awaited()
    assert result["new_announcements"] == 0
