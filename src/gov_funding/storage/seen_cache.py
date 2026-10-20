"""Bedrock 탈락 공고 캐시 (로컬 JSON)

벡터 저장소에는 관련성 임계값을 통과한 공고만 저장한다. 탈락한 공고를 기억하지 않으면
매 실행마다 같은 공고의 상세/첨부를 다시 받아 Bedrock으로 재분석하게 되고,
점수가 임계값 근처에서 흔들리면 며칠 뒤 '신규'로 뒤늦게 알림된다.
"""
import json
import os
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..utils import logger
from ..utils.dates import now_kst

DEFAULT_STATE_DIR = "/home/ubuntu/.local/state/gov-funding"

# 마감일을 모르는 공고의 탈락 판정 유지 기간 (이후 재분석 — 공고 수정/상세 보강 반영).
# 키마다 0~6일을 더해 첫 실행에 몰린 판정이 같은 날 한꺼번에 만료되지 않게 한다.
RECHECK_DAYS = 14
RECHECK_JITTER_DAYS = 7
# 마감일을 아는 공고는 마감일까지 유지하되 이 기간을 넘기지 않는다
MAX_KEEP_DAYS = 60


class SeenCache:
    """Bedrock 탈락 공고 캐시 {벡터 키: {score, canon, last_scored, until}}"""

    def __init__(self, path: Optional[str] = None):
        state_dir = os.environ.get("GOV_FUNDING_STATE_DIR", DEFAULT_STATE_DIR)
        self.path = Path(path or os.path.join(state_dir, "rejected.json"))
        self.entries: dict[str, dict] = {}

    def load(self) -> "SeenCache":
        """캐시 파일 로드 (없거나 깨졌으면 빈 캐시)"""
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            entries = {}
        except (OSError, ValueError) as e:
            logger.warning(f"탈락 캐시 로드 실패 - 빈 캐시로 시작: {e}")
            entries = {}
        if not isinstance(entries, dict):
            logger.warning("탈락 캐시 형식 오류 - 빈 캐시로 시작")
            entries = {}
        self.entries = {k: v for k, v in entries.items() if isinstance(v, dict)}
        return self

    def save(self):
        """캐시 저장 (임시 파일 → rename으로 원자적 교체, 만료 항목 정리)"""
        now = now_kst().isoformat()
        self.entries = {k: v for k, v in self.entries.items() if v.get("until", "") > now}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.entries, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            logger.warning(f"탈락 캐시 저장 실패: {e}")

    def is_rejected(self, key: str, canon: str) -> bool:
        """같은 제목으로 받은 탈락 판정이 아직 유효한지"""
        entry = self.entries.get(key)
        if not entry or entry.get("canon") != canon:
            return False
        try:
            until = datetime.fromisoformat(entry["until"])
        except (KeyError, ValueError):
            return False
        return now_kst() < until

    def record(self, key: str, canon: str, score: float, deadline: Optional[datetime] = None):
        """탈락 판정 기록

        Args:
            deadline: 공고 마감일. 있으면 마감일까지(최대 MAX_KEEP_DAYS일) 재분석하지 않는다.
        """
        now = now_kst()
        if deadline is not None:
            until = min(deadline, now + timedelta(days=MAX_KEEP_DAYS))
        else:
            jitter = zlib.crc32(key.encode()) % RECHECK_JITTER_DAYS
            until = now + timedelta(days=RECHECK_DAYS + jitter)
        self.entries[key] = {
            "score": score,
            "canon": canon,
            "last_scored": now.isoformat(),
            "until": until.isoformat(),
        }

    def discard(self, key: str):
        """통과한 공고는 캐시에서 제거"""
        self.entries.pop(key, None)
