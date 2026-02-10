#!/usr/bin/env python3
"""업무 자동화 워크플로우 스케줄러"""

import subprocess
import logging
import os
import json
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# 로그 디렉토리 생성 (환경에 따라 자동 설정)
HOME_DIR = os.environ.get("HOME", "/home/ec2-user")
LOG_DIR = os.path.join(HOME_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Claude CLI 경로 (systemd 환경에서 PATH에 없을 수 있음)
CLAUDE_PATH = os.path.join(HOME_DIR, ".local", "bin", "claude")

# 스케줄러 전용 세션 ID 파일 (--resume 용)
SESSION_FILE = os.path.join(LOG_DIR, "scheduler_session_id.txt")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

WORKFLOW_PROMPT = """
⚠️ 중요: 이것은 정기 실행입니다. 이전 세션에서 이미 실행한 적이 있더라도, 아래 모든 단계를 처음부터 다시 실행해야 합니다. 이전 결과를 요약하거나 참조만 하지 마세요. 반드시 MCP 도구를 호출하여 최신 데이터를 조회하고, 4단계까지 모두 완료하세요.

다음 워크플로우를 순서대로 실행해주세요:

## 1단계: Notion Kanban 확인
MCP notion 도구(mcp__notion__*)를 직접 호출하여:
- Searchdoc Workspace에서 Kanban 보드 검색
- **Shawn이 Assignee 또는 Reviewer인 태스크만 필터링**
- Ready, In Progress, In Review 상태의 모든 태스크 조회
- **오늘 마감인 태스크는 🔴 긴급으로 강조 표시**
- 기한 지난 태스크 알림
- 📊 이전 실행 결과와 비교하여 **변경사항** 표시 (새로 추가된 태스크, 상태 변경, 완료된 태스크)

## 2단계: Gmail 확인 및 라벨링
MCP gmail 도구(mcp__gmail__*)를 직접 호출하여:
- 최근 1시간 내 새 메일 검색 (newer_than:1h)
- 중요 메일 필터링 (is:important)
- 미읽음 메일 요약
- **사용자 라벨이 없는 이메일에 아래 규칙에 따라 라벨 적용:**

### Gmail 라벨링 규칙 (발신자 기준)
| 발신자 패턴 | 적용할 라벨 |
|-------------|-------------|
| Gemini, Google Meet (회의록) | `회의록` |
| LT Kanban, LT meeting | `회의록`, `Searchdoc LT` |
| Google Payments, 인보이스 | `CSP`, `CSP/GCP`, `청구서` |
| Microsoft Azure | `CSP`, `CSP/Azure` |
| AWS | `CSP`, `CSP/AWS` |
| API 변경, 서비스 업데이트 공지 | `서비스공지` |
| 모두싸인, 전자서명 | `전자서명`, `Hiring` |
| 직행, 채용 플랫폼 | `Hiring` |
| 인크루트, JetBrains 등 뉴스레터 | `뉴스레터` |
| 성남산업진흥원, KISED 등 정부기관 | `정부지원사업`, `파트너` |
| VC, 투자사 | `VC` |
| 고객사 | `고객` |

## 3단계: 메일 초안 작성 (절대 전송 금지)
MCP gmail 도구(mcp__gmail__gmail_create_draft, mcp__gmail__gmail_create_reply_draft)를 직접 호출하여:
- 응답이 필요한 메일 식별
- **본인 이메일: shawn.kim@searchdoc.ai**
- **다음 메일은 초안 작성 대상에서 제외:**
  - 내가 보낸 메일 (from이 @searchdoc.ai 도메인인 경우)
  - 내가 직접 받은 메일이 아닌 경우 (To에 shawn.kim@searchdoc.ai가 없는 메일)
  - 참조(CC/BCC)에만 포함된 메일
- **초안 작성 대상:** To에 shawn.kim@searchdoc.ai가 직접 포함된 외부 발신 메일만 해당
- 각 메일에 대해 초안만 작성 (Gmail Drafts에 저장)
- 초안 목록 생성
- **중요: 메일을 절대 전송하지 마세요. 초안 생성만 허용됩니다.**

## 4단계: 결과 보고 (Slack DM) — 반드시 실행!
MCP slack 도구(mcp__slack__slack_post_message)를 직접 호출하여 **반드시 개인 DM**으로 전송:
- **Shawn Kim User ID: U09169NDUKA** (채널 ID가 아님!)
- channel 파라미터에 **U09169NDUKA** 사용
- ⚠️ 절대 C로 시작하는 채널 ID 사용 금지
- 보고 내용에 **이전 실행 대비 변경사항**을 포함:
  - 🆕 새로운 항목 (새 태스크, 새 메일)
  - 🔄 변경된 항목 (태스크 상태 변경 등)
  - ✅ 완료/처리된 항목
- 변경사항이 없으면 "이전 실행 대비 변경사항 없음"으로 표시
"""

def format_event(event):
    """JSON 이벤트를 읽기 쉬운 형태로 포맷"""
    event_type = event.get("type", "unknown")
    lines = [f"[{event_type}]"]

    if event_type == "assistant":
        msg = event.get("message", {})
        for content in msg.get("content", []):
            if content.get("type") == "text":
                text = content.get("text", "")
                lines.append(f"  텍스트: {text[:500]}{'...' if len(text) > 500 else ''}")
            elif content.get("type") == "tool_use":
                lines.append(f"  도구 호출: {content.get('name')}")
                input_str = json.dumps(content.get("input", {}), ensure_ascii=False)
                lines.append(f"  입력: {input_str[:500]}{'...' if len(input_str) > 500 else ''}")

    elif event_type == "result":
        result_str = json.dumps(event, ensure_ascii=False)
        lines.append(f"  결과: {result_str[:1000]}{'...' if len(result_str) > 1000 else ''}")

    elif event_type == "system":
        lines.append(f"  {json.dumps(event, ensure_ascii=False)}")

    else:
        event_str = json.dumps(event, ensure_ascii=False)
        lines.append(f"  {event_str[:500]}{'...' if len(event_str) > 500 else ''}")

    return "\n".join(lines)


def get_saved_session_id():
    """저장된 스케줄러 전용 세션 ID 반환"""
    try:
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r") as f:
                sid = f.read().strip()
                if sid:
                    return sid
    except Exception:
        pass
    return None


def save_session_id(session_id):
    """스케줄러 전용 세션 ID 저장"""
    try:
        with open(SESSION_FILE, "w") as f:
            f.write(session_id)
        logger.info(f"세션 ID 저장: {session_id}")
    except Exception as e:
        logger.error(f"세션 ID 저장 실패: {e}")


def run_workflow():
    """Claude CLI로 워크플로우 실행 (실시간 로그)"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    detail_log = os.path.join(LOG_DIR, f"workflow_{timestamp}.log")
    raw_log = os.path.join(LOG_DIR, f"workflow_{timestamp}.json")

    logger.info(f"워크플로우 시작 - 실시간 로그: tail -f {detail_log}")

    # 이전 세션이 있으면 --resume, 없으면 새 세션
    saved_sid = get_saved_session_id()
    cmd = [CLAUDE_PATH, "-p", "--model", "opus", WORKFLOW_PROMPT]
    if saved_sid:
        cmd += ["--resume", saved_sid]
        logger.info(f"이전 세션 이어서 실행: {saved_sid}")
    else:
        logger.info("새 세션으로 실행")
    cmd += [
        "--dangerously-skip-permissions",
        "--verbose",
        "--output-format", "stream-json"
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.join(HOME_DIR, "slack-bots")
        )

        # 실시간으로 stdout 읽고 파일에 쓰기
        with open(detail_log, "w") as log_f, open(raw_log, "w") as raw_f:
            log_f.write(f"=== 워크플로우 실행: {timestamp} ===\n\n")
            log_f.flush()

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                # 원본 JSON 저장
                raw_f.write(line + "\n")
                raw_f.flush()

                # 포맷된 로그 저장
                try:
                    event = json.loads(line)
                    formatted = format_event(event)
                    log_f.write(formatted + "\n\n")

                    # result 이벤트에서 session_id 추출 후 저장
                    if event.get("type") == "result":
                        sid = event.get("session_id")
                        if sid:
                            save_session_id(sid)
                except json.JSONDecodeError:
                    log_f.write(f"[RAW] {line}\n\n")
                log_f.flush()

            # stderr 저장
            stderr = process.stderr.read()
            log_f.write("\n=== STDERR ===\n")
            log_f.write(stderr or "(없음)")

            process.wait()
            log_f.write(f"\n\n=== Return Code: {process.returncode} ===\n")

        if process.returncode == 0:
            logger.info(f"워크플로우 완료 - 상세 로그: {detail_log}")
        else:
            logger.error(f"워크플로우 실패 - 상세 로그: {detail_log}")

    except Exception as e:
        logger.error(f"워크플로우 에러: {e}")

def main():
    scheduler = BlockingScheduler()

    # 평일 9시부터 2시간 간격 실행 (9, 11, 13, 15, 17시)
    scheduler.add_job(
        run_workflow,
        CronTrigger(day_of_week='mon-fri', hour='9,11,13,15,17', minute=0, timezone='Asia/Seoul'),
        id='hourly_workflow',
        name='Bi-hourly Task Workflow'
    )

    logger.info("스케줄러 시작: 평일 9시부터 2시간 간격 (9, 11, 13, 15, 17시)")
    logger.info("즉시 1회 테스트 실행...")
    run_workflow()

    scheduler.start()

if __name__ == "__main__":
    main()
