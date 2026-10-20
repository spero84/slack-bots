"""SeenCache(Bedrock 탈락 캐시) 테스트"""
import json
from datetime import datetime, timedelta

from src.gov_funding.storage import seen_cache as seen_cache_module
from src.gov_funding.storage.seen_cache import MAX_KEEP_DAYS, RECHECK_DAYS, RECHECK_JITTER_DAYS, SeenCache


def test_record_and_reload(tmp_path):
    path = tmp_path / "rejected.json"
    cache = SeenCache(str(path)).load()
    cache.record("bizinfo_PBLN_1", "ai지원", 0.4)
    cache.save()

    reloaded = SeenCache(str(path)).load()
    assert reloaded.is_rejected("bizinfo_PBLN_1", "ai지원")
    assert not reloaded.is_rejected("bizinfo_PBLN_1", "제목바뀜")
    assert not reloaded.is_rejected("bizinfo_PBLN_2", "ai지원")


def test_expires_after_recheck_days(tmp_path, monkeypatch):
    cache = SeenCache(str(tmp_path / "rejected.json"))
    cache.record("nia_1", "t", 0.3)

    later = datetime.now() + timedelta(days=RECHECK_DAYS + RECHECK_JITTER_DAYS + 1)
    monkeypatch.setattr(seen_cache_module, "now_kst", lambda: later)

    assert not cache.is_rejected("nia_1", "t")
    cache.save()
    assert json.loads((tmp_path / "rejected.json").read_text()) == {}


def test_discard(tmp_path):
    cache = SeenCache(str(tmp_path / "rejected.json"))
    cache.record("nia_1", "t", 0.3)
    cache.discard("nia_1")
    assert not cache.is_rejected("nia_1", "t")


def test_corrupt_file_starts_empty(tmp_path):
    path = tmp_path / "rejected.json"
    path.write_text("{not json")
    assert SeenCache(str(path)).load().entries == {}


def test_state_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GOV_FUNDING_STATE_DIR", str(tmp_path))
    assert SeenCache().path == tmp_path / "rejected.json"


def test_expiry_is_spread_across_keys(tmp_path):
    """첫 실행에 몰린 판정이 같은 날 한꺼번에 만료되지 않게 키마다 만료일이 다르다"""
    cache = SeenCache(str(tmp_path / "rejected.json"))
    for i in range(50):
        cache.record(f"kstartup_{i}", "t", 0.3)
    expiry_days = {e["until"][:10] for e in cache.entries.values()}
    assert len(expiry_days) > 1


def test_rejection_kept_until_deadline(tmp_path, monkeypatch):
    now = datetime(2026, 10, 20, 9, 0)
    monkeypatch.setattr(seen_cache_module, "now_kst", lambda: now)
    cache = SeenCache(str(tmp_path / "rejected.json"))
    cache.record("bizinfo_1", "t", 0.4, deadline=datetime(2026, 11, 30, 23, 59, 59))
    cache.record("bizinfo_2", "t", 0.4, deadline=datetime(2027, 6, 30, 23, 59, 59))

    monkeypatch.setattr(seen_cache_module, "now_kst", lambda: datetime(2026, 11, 25))
    assert cache.is_rejected("bizinfo_1", "t")  # 14일 지나도 마감 전이면 유지
    assert cache.entries["bizinfo_2"]["until"][:10] == (now + timedelta(days=MAX_KEEP_DAYS)).date().isoformat()

    monkeypatch.setattr(seen_cache_module, "now_kst", lambda: datetime(2026, 12, 1))
    assert not cache.is_rejected("bizinfo_1", "t")


def test_non_dict_file_starts_empty(tmp_path):
    path = tmp_path / "rejected.json"
    path.write_text("[1, 2, 3]")
    assert SeenCache(str(path)).load().entries == {}
