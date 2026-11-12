"""aec-news 실행 상태(전송 이력, 탈락 캐시, 마지막 성공 시각) 테스트"""
import json
from datetime import datetime, timedelta, timezone

from src.aec_news.state import REJECT_KEEP_DAYS, SENT_KEEP_DAYS, RunState

NOW = datetime(2026, 11, 12, 23, 30, tzinfo=timezone.utc)


def test_missing_file_gives_empty_state(tmp_path):
    state = RunState(str(tmp_path / "none.json")).load()
    assert state.last_success is None
    assert state.sent == {}
    assert state.judged == {}


def test_corrupt_file_gives_empty_state(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = RunState(str(path)).load()
    assert state.sent == {} and state.last_success is None


def test_non_dict_sections_are_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_success": 3, "sent": [1, 2], "judged": "x"}), encoding="utf-8")
    state = RunState(str(path)).load()
    assert state.last_success is None and state.sent == {} and state.judged == {}


def test_roundtrip_and_sent_lookup(tmp_path):
    path = tmp_path / "sub" / "state.json"
    state = RunState(str(path)).load()
    state.last_success = NOW
    state.mark_sent(["gid:abc", "url:donga.com/news/1", ""])
    state.save()

    loaded = RunState(str(path)).load()
    assert loaded.last_success == NOW
    assert loaded.was_sent(["gid:zzz", "url:donga.com/news/1"])
    assert not loaded.was_sent(["gid:zzz"])
    assert "" not in loaded.sent
    assert not path.with_suffix(".tmp").exists()


def test_old_sent_entries_are_pruned(tmp_path):
    path = tmp_path / "state.json"
    state = RunState(str(path)).load()
    old = datetime.now(timezone.utc) - timedelta(days=SENT_KEEP_DAYS + 1)
    state.mark_sent(["gid:old"], when=old)
    state.mark_sent(["gid:new"])
    state.save()
    loaded = RunState(str(path)).load()
    assert "gid:old" not in loaded.sent
    assert "gid:new" in loaded.sent


def test_rejected_cache_expires(tmp_path):
    state = RunState(str(tmp_path / "s.json")).load()
    state.record_rejected("gid:x", 0.1, now=NOW)
    assert state.is_rejected("gid:x", now=NOW + timedelta(days=REJECT_KEEP_DAYS - 1))
    assert not state.is_rejected("gid:x", now=NOW + timedelta(days=REJECT_KEEP_DAYS + 1))
    assert not state.is_rejected("gid:unknown", now=NOW)


def test_naive_timestamps_are_treated_as_utc(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_success": "2026-11-12T00:00:00"}), encoding="utf-8")
    state = RunState(str(path)).load()
    assert state.last_success == datetime(2026, 11, 12, tzinfo=timezone.utc)
