"""실행 상태 저장 (로컬 JSON)

- last_success: 마지막으로 Slack 전송까지 끝난 실행의 수집 기준 시각. 다음 실행의 수집 시작점이다.
- sent: 이미 보낸 기사 키 → 보낸 시각. 다음 날 같은 기사가 검색 결과에 다시 나와도 재전송하지 않는다.
  Google News는 미국 언론사 검색에서 `when:` 기간 조건을 자주 무시하므로 날짜 필터만으로는 부족하다.
- judged: Bedrock이 관련 없다고 판정한 기사 키 → 만료 시각. 같은 기사를 매일 다시 판정하지 않는다.
- decoded: Google News 기사 ID → 원문 URL. 복원에 요청 2회가 들어 결과를 재사용한다.

파일은 임시 파일에 쓴 뒤 rename으로 교체한다 (실행 중 중단돼도 깨진 JSON이 남지 않음).
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .logger import logger

DEFAULT_STATE_DIR = "/home/ubuntu/.local/state/aec-news"
SENT_KEEP_DAYS = 45
REJECT_KEEP_DAYS = 10
DECODED_KEEP_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class RunState:
    """{last_success, sent: {key: iso}, judged: {key: {score, until}}, decoded: {google_id: {url, at}}}"""

    def __init__(self, path: Optional[str] = None):
        state_dir = os.environ.get("AEC_NEWS_STATE_DIR", DEFAULT_STATE_DIR)
        self.path = Path(path or os.path.join(state_dir, "state.json"))
        self.last_success: Optional[datetime] = None
        self.sent: dict[str, str] = {}
        self.judged: dict[str, dict] = {}
        self.decoded: dict[str, dict] = {}

    def load(self) -> "RunState":
        """상태 파일 로드 (없거나 깨졌으면 빈 상태)"""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as e:
            logger.warning(f"상태 파일 로드 실패 - 빈 상태로 시작: {e}")
            data = {}
        if not isinstance(data, dict):
            logger.warning("상태 파일 형식 오류 - 빈 상태로 시작")
            data = {}
        self.last_success = _parse(data.get("last_success"))
        sent = data.get("sent")
        self.sent = {k: v for k, v in sent.items() if isinstance(v, str)} if isinstance(sent, dict) else {}
        judged = data.get("judged")
        self.judged = (
            {k: v for k, v in judged.items() if isinstance(v, dict)} if isinstance(judged, dict) else {}
        )
        decoded = data.get("decoded")
        self.decoded = (
            {k: v for k, v in decoded.items() if isinstance(v, dict) and isinstance(v.get("url"), str)}
            if isinstance(decoded, dict) else {}
        )
        return self

    def _prune(self, now: datetime):
        sent_cutoff = now - timedelta(days=SENT_KEEP_DAYS)
        self.sent = {k: v for k, v in self.sent.items() if (_parse(v) or now) > sent_cutoff}
        self.judged = {
            k: v for k, v in self.judged.items() if (_parse(v.get("until")) or now) > now
        }
        decoded_cutoff = now - timedelta(days=DECODED_KEEP_DAYS)
        self.decoded = {k: v for k, v in self.decoded.items() if (_parse(v.get("at")) or now) > decoded_cutoff}

    def save(self):
        """상태 저장 (만료 항목 정리 후 원자적 교체). 실패해도 예외를 올리지 않는다."""
        now = _now()
        self._prune(now)
        data = {
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "sent": self.sent,
            "judged": self.judged,
            "decoded": self.decoded,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            logger.warning(f"상태 파일 저장 실패: {e}")

    # --- 전송 이력 ---

    def was_sent(self, keys: Iterable[str]) -> bool:
        return any(k in self.sent for k in keys if k)

    def mark_sent(self, keys: Iterable[str], when: Optional[datetime] = None):
        stamp = (when or _now()).isoformat()
        for k in keys:
            if k:
                self.sent[k] = stamp

    # --- 탈락 판정 캐시 ---

    def is_rejected(self, key: str, now: Optional[datetime] = None) -> bool:
        entry = self.judged.get(key)
        if not entry:
            return False
        until = _parse(entry.get("until"))
        return until is not None and (now or _now()) < until

    def record_rejected(self, key: str, score: float, now: Optional[datetime] = None):
        now = now or _now()
        self.judged[key] = {
            "score": round(float(score), 3),
            "until": (now + timedelta(days=REJECT_KEEP_DAYS)).isoformat(),
        }

    # --- 원문 URL 캐시 ---

    def decode_cache(self) -> dict[str, str]:
        return {k: v["url"] for k, v in self.decoded.items()}

    def update_decoded(self, cache: dict[str, str], now: Optional[datetime] = None):
        stamp = (now or _now()).isoformat()
        for key, url in cache.items():
            if key not in self.decoded or self.decoded[key].get("url") != url:
                self.decoded[key] = {"url": url, "at": stamp}
