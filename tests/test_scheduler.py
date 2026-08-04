"""스케줄러 타임스탬프 관리 단위 테스트"""

import json
import os
import time
from unittest.mock import patch

import pytest

from src.scheduler.scheduler import (
    DEFAULT_LOOKBACK_SECONDS,
    MAX_LOOKBACK_SECONDS,
    build_workflow_prompt,
    load_last_run_timestamp,
    save_last_run_timestamp,
)


@pytest.fixture
def last_run_file(tmp_path):
    """임시 디렉토리에 LAST_RUN_FILE 경로 생성."""
    filepath = tmp_path / "scheduler_last_run.json"
    with patch("src.scheduler.scheduler.LAST_RUN_FILE", str(filepath)):
        yield str(filepath)


class TestLoadLastRunTimestamp:
    """load_last_run_timestamp 함수 테스트"""

    def test_파일_없을_때_기본_24시간_전(self, last_run_file):
        """파일이 없으면 24시간 전 epoch 반환."""
        now = time.time()
        result = load_last_run_timestamp()
        expected = now - DEFAULT_LOOKBACK_SECONDS
        assert abs(result - expected) < 2  # 실행 시간 오차 허용

    def test_정상_파일_로드(self, last_run_file):
        """정상 JSON 파일에서 epoch 로드."""
        two_hours_ago = time.time() - 7200
        data = {"last_run_epoch": two_hours_ago, "last_run_iso": "2026-02-26T09:00:00+09:00"}
        with open(last_run_file, "w") as f:
            json.dump(data, f)

        result = load_last_run_timestamp()
        assert abs(result - two_hours_ago) < 1

    def test_손상된_JSON_기본값(self, last_run_file):
        """손상된 JSON → 24시간 전 기본값."""
        with open(last_run_file, "w") as f:
            f.write("not valid json{{{")

        now = time.time()
        result = load_last_run_timestamp()
        expected = now - DEFAULT_LOOKBACK_SECONDS
        assert abs(result - expected) < 2

    def test_키_누락_기본값(self, last_run_file):
        """last_run_epoch 키 없음 → 24시간 전 기본값."""
        with open(last_run_file, "w") as f:
            json.dump({"wrong_key": 12345}, f)

        now = time.time()
        result = load_last_run_timestamp()
        expected = now - DEFAULT_LOOKBACK_SECONDS
        assert abs(result - expected) < 2

    def test_미래_타임스탬프_기본값(self, last_run_file):
        """미래 타임스탬프 → 24시간 전 기본값."""
        future_ts = time.time() + 3600  # 1시간 뒤
        with open(last_run_file, "w") as f:
            json.dump({"last_run_epoch": future_ts}, f)

        now = time.time()
        result = load_last_run_timestamp()
        expected = now - DEFAULT_LOOKBACK_SECONDS
        assert abs(result - expected) < 2

    def test_72시간_초과_제한(self, last_run_file):
        """72시간 이상 지난 타임스탬프 → 72시간으로 cap."""
        old_ts = time.time() - 400000  # ~4.6일 전
        with open(last_run_file, "w") as f:
            json.dump({"last_run_epoch": old_ts}, f)

        now = time.time()
        result = load_last_run_timestamp()
        expected = now - MAX_LOOKBACK_SECONDS
        assert abs(result - expected) < 2

    def test_주말_64시간_갭_정상처리(self, last_run_file):
        """주말 갭(64시간) → 72시간 이내이므로 정상 반환."""
        weekend_gap_ts = time.time() - 64 * 3600  # 64시간 전
        with open(last_run_file, "w") as f:
            json.dump({"last_run_epoch": weekend_gap_ts}, f)

        result = load_last_run_timestamp()
        assert abs(result - weekend_gap_ts) < 1

    def test_야간_16시간_갭_정상처리(self, last_run_file):
        """야간 갭(16시간) → 정상 반환."""
        overnight_ts = time.time() - 16 * 3600  # 16시간 전
        with open(last_run_file, "w") as f:
            json.dump({"last_run_epoch": overnight_ts}, f)

        result = load_last_run_timestamp()
        assert abs(result - overnight_ts) < 1

    def test_epoch_문자열_타입_변환(self, last_run_file):
        """epoch 값이 문자열이어도 float 변환."""
        two_hours_ago = time.time() - 7200
        with open(last_run_file, "w") as f:
            json.dump({"last_run_epoch": str(two_hours_ago)}, f)

        result = load_last_run_timestamp()
        assert abs(result - two_hours_ago) < 1


class TestSaveLastRunTimestamp:
    """save_last_run_timestamp 함수 테스트"""

    def test_정상_저장(self, last_run_file):
        """타임스탬프가 JSON으로 저장되는지 확인."""
        save_last_run_timestamp()

        assert os.path.exists(last_run_file)
        with open(last_run_file, "r") as f:
            data = json.load(f)

        assert "last_run_epoch" in data
        assert "last_run_iso" in data
        assert abs(data["last_run_epoch"] - time.time()) < 2

    def test_저장_후_로드_왕복(self, last_run_file):
        """저장 후 로드하면 현재 시각에 가까운 값."""
        save_last_run_timestamp()
        result = load_last_run_timestamp()
        assert abs(result - time.time()) < 2

    def test_ISO_형식_포함(self, last_run_file):
        """ISO 형식 문자열이 포함되는지 확인."""
        save_last_run_timestamp()

        with open(last_run_file, "r") as f:
            data = json.load(f)

        # ISO 형식에 +09:00 (KST) 포함 확인
        assert "+09:00" in data["last_run_iso"]


class TestBuildWorkflowPrompt:
    """build_workflow_prompt 함수 테스트"""

    def test_after_epoch_포함(self):
        """프롬프트에 after:epoch가 포함되는지 확인."""
        epoch = 1740000000
        prompt = build_workflow_prompt(epoch)
        assert f"after:{epoch}" in prompt

    def test_사람읽기_가능_시각_포함(self):
        """프롬프트에 KST 시각 문자열이 포함되는지 확인."""
        epoch = 1740000000
        prompt = build_workflow_prompt(epoch)
        assert "KST" in prompt
        # YYYY-MM-DD HH:MM 형식 확인
        assert "2025-02-20" in prompt  # epoch 1740000000의 KST 날짜

    def test_newer_than_없음(self):
        """newer_than:1h가 더 이상 포함되지 않는지 확인."""
        epoch = 1740000000
        prompt = build_workflow_prompt(epoch)
        assert "newer_than:1h" not in prompt

    def test_워크플로우_4단계_모두_포함(self):
        """프롬프트에 4단계가 모두 포함되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "1단계: Notion Kanban 확인" in prompt
        assert "2단계: Gmail 확인 및 라벨링" in prompt
        assert "3단계: 메일 초안 작성" in prompt
        assert "4단계: 결과 보고" in prompt

    def test_슬랙_채널_ID_포함(self):
        """프롬프트에 Slack 채널 ID가 포함되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "C0AEW7LF0RJ" in prompt

    def test_반환_타입_문자열(self):
        """반환값이 str인지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert isinstance(prompt, str)


class TestKanbanBoardScope:
    """1단계 Notion Kanban 조회 대상 보드 테스트"""

    def test_대상_3개_보드_모두_명시(self):
        """조회 대상 3개 보드명이 프롬프트에 포함되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "Kanban : LT Operation" in prompt
        assert "Kanban: Management Support" in prompt
        assert "Searchdoc Operation Board" in prompt

    def test_3개_보드_data_source_id_포함(self):
        """3개 보드의 확정된 data_source_id가 모두 포함되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "24431fc3-cdaa-81da-8b9b-000be37b9905" in prompt  # LT Operation
        assert "33631fc3-cdaa-8157-b790-000b885efad5" in prompt  # Management Support
        assert "abc31fc3-cdaa-8318-9439-87d69ae43b48" in prompt  # Searchdoc Operation

    def test_Shawn_user_id_포함(self):
        """담당자 필터용 Shawn의 Notion user_id가 포함되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "20cd872b-594c-810b-9d99-0002e207a7c1" in prompt

    def test_보드별_담당자_속성명_포함(self):
        """보드마다 다른 담당자 속성명이 모두 명시되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "Reviewer" in prompt  # Management Support 전용
        assert "Person" in prompt    # Searchdoc Operation 전용

    def test_보드별_마감_속성명_포함(self):
        """보드마다 다른 마감일 속성명이 모두 명시되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "Start & Due Date" in prompt  # LT Operation
        assert "Deadline" in prompt          # Management Support
        assert "Due Date" in prompt          # Searchdoc Operation

    def test_상태값_Ready_Commit_형식(self):
        """실제 상태값 'Ready (Commit)'이 사용되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "Ready (Commit)" in prompt
        assert "In Progress" in prompt
        assert "In Review" in prompt

    def test_구_보드명_제거(self):
        """이전 예시 보드명(Product, LT Internal)이 남아있지 않은지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "LT Internal" not in prompt
        assert "보드: Product" not in prompt

    def test_보드_한정_지시_포함(self):
        """3개 보드만 조회하라는 지시가 포함되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "다른 보드는 조회하지 않는다" in prompt

    def test_조회_실패_보고_지시_포함(self):
        """보드 조회 실패 시 보고서에 명시하라는 지시가 포함되는지 확인."""
        prompt = build_workflow_prompt(1740000000)
        assert "보드 조회 실패" in prompt
