#!/usr/bin/env python3
"""업무 자동화 워크플로우 스케줄러"""

import subprocess
import logging
import os
import json
import time
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# 로그 디렉토리 생성 (환경에 따라 자동 설정)
HOME_DIR = os.environ.get("HOME", "/home/ec2-user")
LOG_DIR = os.path.join(HOME_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Claude CLI 경로 (systemd 환경에서 PATH에 없을 수 있음)
CLAUDE_PATH = os.path.join(HOME_DIR, ".local", "bin", "claude")

LAST_RUN_FILE = os.path.join(LOG_DIR, "scheduler_last_run.json")
DEFAULT_LOOKBACK_SECONDS = 86400   # 파일 없을 때 기본 24시간
MAX_LOOKBACK_SECONDS = 259200      # 최대 72시간 (주말 커버)

KST = timezone(timedelta(hours=9))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def load_last_run_timestamp() -> float:
    """마지막 실행 시각(epoch)을 파일에서 로드.

    파일 없음/손상/미래시각 → 24시간 전 기본값.
    72시간 초과 → 72시간으로 cap.
    """
    now = time.time()
    default_ts = now - DEFAULT_LOOKBACK_SECONDS

    try:
        with open(LAST_RUN_FILE, "r") as f:
            data = json.load(f)
        last_run = float(data["last_run_epoch"])

        # 미래 타임스탬프 방지
        if last_run > now:
            logger.warning(f"미래 타임스탬프 감지 ({last_run}), 기본값 사용")
            return default_ts

        # 72시간 초과 제한
        if now - last_run > MAX_LOOKBACK_SECONDS:
            logger.warning(f"마지막 실행이 72시간 초과, 72시간으로 제한")
            return now - MAX_LOOKBACK_SECONDS

        return last_run

    except FileNotFoundError:
        logger.info("마지막 실행 기록 없음, 기본 24시간 전부터 검색")
        return default_ts
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning(f"마지막 실행 파일 손상: {e}, 기본값 사용")
        return default_ts


def save_last_run_timestamp():
    """현재 시각을 마지막 실행 타임스탬프로 저장."""
    now = time.time()
    data = {
        "last_run_epoch": now,
        "last_run_iso": datetime.fromtimestamp(now, tz=KST).isoformat(),
    }
    try:
        with open(LAST_RUN_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"실행 타임스탬프 저장: {data['last_run_iso']}")
    except OSError as e:
        logger.error(f"타임스탬프 저장 실패: {e}")


def build_workflow_prompt(gmail_after_epoch: int) -> str:
    """워크플로우 프롬프트를 Gmail 검색 시간 범위와 함께 동적 생성."""
    after_dt = datetime.fromtimestamp(gmail_after_epoch, tz=KST)
    after_str = after_dt.strftime("%Y-%m-%d %H:%M KST")

    return f"""
다음 워크플로우를 순서대로 실행해주세요. 반드시 MCP 도구를 호출하여 최신 데이터를 조회하고, 4단계까지 모두 완료하세요.

## 1단계: Notion Kanban 확인
MCP notion 도구(mcp__notion__*)를 직접 호출하여:
- Searchdoc Workspace에서 Kanban 보드 검색
- **Shawn이 Assignee 또는 Reviewer인 태스크만 필터링**
- Ready, In Progress, In Review 상태의 모든 태스크 조회
- **오늘 마감인 태스크는 🔴 긴급으로 강조 표시**
- 기한 지난 태스크 알림

## 2단계: Gmail 확인 및 라벨링
MCP gmail 도구(mcp__gmail__*)를 직접 호출하여:
- {after_str} 이후 새 메일 검색 (after:{gmail_after_epoch})
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
MCP gmail 도구(mcp__gmail__gmail_create_draft, mcp__gmail__gmail_create_reply_draft, mcp__gmail__gmail_get_thread)를 직접 호출하여:
- 응답이 필요한 메일 식별
- **본인 이메일: shawn.kim@searchdoc.ai**
- **다음 메일은 초안 작성 대상에서 제외:**
  - 내가 보낸 메일 (from이 @searchdoc.ai 도메인인 경우)
  - 내가 직접 받은 메일이 아닌 경우 (To에 shawn.kim@searchdoc.ai가 없는 메일)
  - 참조(CC/BCC)에만 포함된 메일
- **초안 작성 대상:** To에 shawn.kim@searchdoc.ai가 직접 포함된 외부 발신 메일만 해당
- **중요: Gmail API 응답의 `to` 필드를 반드시 확인하세요.**
  - `to` 배열에 shawn.kim@searchdoc.ai가 포함되어 있지 않으면 → 초안 작성 제외
  - 예: to=["joonsun@searchdoc.ai"] → 내가 받은 메일이 아님 → 제외!
  - 예: to=["shawn.kim@searchdoc.ai"] → 초안 작성 대상 ✅
- **회신 초안 작성 시 반드시 스레드 전체를 먼저 읽으세요:**
  1. 메일의 `threadId`를 확인
  2. `mcp__gmail__gmail_get_thread`로 해당 스레드의 전체 대화 내역 조회
  3. 전체 맥락을 파악한 후 `mcp__gmail__gmail_create_reply_draft`로 회신 초안 작성
- 각 메일에 대해 초안만 작성 (Gmail Drafts에 저장)
- 초안 목록 생성
- **중요: 메일을 절대 전송하지 마세요. 초안 생성만 허용됩니다.**
- **초안 형식:** 비즈니스 메일 형식으로 작성 (서명은 Gmail에서 자동 첨부됩니다):
  - 인사: "안녕하십니까? Searchdoc의 김성헌입니다."
  - 본문
  - 맺음: "감사합니다.<br><br>김성헌 드림"
  - **반드시 `is_html: true`로 설정하세요** (서명이 HTML 형식으로 자동 첨부됩니다)
  - **서명은 자동으로 첨부됩니다. 본문에 서명을 직접 작성하지 마세요.**
  - **HTML 본문에 <![CDATA[...]]> 같은 XML 구문을 절대 사용하지 마세요.** 순수 HTML만 작성하세요.

## 4단계: 결과 보고 (Slack 채널) — 반드시 실행!
MCP slack 도구(mcp__slack__slack_post_message)를 직접 호출하여 **전용 업무 보고 채널**로 전송:
- **채널 ID: C0AEW7LF0RJ**
- channel 파라미터에 **C0AEW7LF0RJ** 사용
- **마지막 섹션에 "🎯 오늘의 액션 가이드" 포함 (필수)**:
  1~3단계 결과를 종합 분석하여, 긴급도·중요도 순으로 해야 할 일을 구체적으로 안내

### Slack 포맷팅 규칙 (필수 — 반드시 준수!)

🚫 Slack에서 지원하지 않는 Markdown 문법 (절대 사용 금지):
- `| 컬럼 | 컬럼 |` 형태의 마크다운 테이블 ← 절대 금지!
- `---` 수평선 ← Slack에서 렌더링 안 됨
- `### 헤더` ← Slack에서 렌더링 안 됨
- `[텍스트](url)` ← Slack에서 렌더링 안 됨

✅ Slack에서 사용 가능한 문법:
- `*볼드*` (별표로 감싸기)
- `_이탤릭_` (밑줄로 감싸기)
- `` `코드` `` (백틱으로 감싸기)
- `> 인용` (꺾쇠로 인용)
- `<url|텍스트>` (링크)
- `• 항목` (불릿 포인트)
- `━━━━━━━━━━━━━━━━━━━━━━━━` (유니코드 구분선)

❌ BAD (이렇게 쓰면 Slack에서 깨짐):
| 상태 | 태스크 | 보드 | 마감 |
|------|--------|------|------|
| In Progress | 태스크 A | Product | 2025-02-12 |

✅ GOOD (이렇게 써야 Slack에서 정상 표시):
*`In Progress` (2건)*
> • *태스크 A* — 보드: Product · 마감: 2025-02-12
> • *태스크 B* — 보드: LT Internal · 마감: 2025-02-13

보고서 전체 구조 (아래 순서와 형식을 정확히 따를 것):

*📋 Searchdoc 일일 업무 요약*
📅 날짜와 시간을 첫 줄에 표시

━━━━━━━━━━━━━━━━━━━━━━━━

*1️⃣ Notion Kanban 태스크 현황*
⚠️ 절대 테이블 형식 사용 금지. 반드시 아래 불릿 포인트 형식으로 표시.
상태별로 그룹화하여 표시:

*`In Progress` (3건)*
> • *태스크 제목* — 보드: Product · 마감: 2025-02-12
> • *태스크 제목* — 보드: LT Internal · 마감: 2025-02-13

*`Ready` (2건)*
> • *태스크 제목* — 보드: Product · 마감: 2025-02-15

🔴 *긴급/기한 초과*
> • 🚨 *긴급 태스크 제목* — 마감: *오늘* · 보드: LT Internal
> • ⚠️ *기한 초과 태스크 제목* — 마감: 2025-02-09 (*2일 초과*) · 보드: Product

━━━━━━━━━━━━━━━━━━━━━━━━

*2️⃣ Gmail*
⚠️ 절대 테이블 형식 사용 금지. 반드시 아래 불릿 포인트 형식으로 표시.
새 메일이 없으면 "새 메일 없음"으로 표시.

> 📩 *메일 제목* — 발신자명
>     라벨: `CSP`, `청구서` · 미읽음
> 📩 *메일 제목 2* — 발신자명
>     라벨: `뉴스레터` · 읽음

━━━━━━━━━━━━━━━━━━━━━━━━

*3️⃣ 메일 초안*
초안이 없으면 "작성 대상 없음"으로 표시.
⚠️ 절대 테이블 형식 사용 금지. 반드시 아래 불릿 포인트 형식으로 표시.

> ✏️ *초안 제목* → 수신자
>     원본: 메일 제목 요약
> ✏️ *회의 일정 확인 회신* → 김재순 과장
>     원본: Rerank 모델 도입 검토 관련 기술 논의 요청

━━━━━━━━━━━━━━━━━━━━━━━━

*4️⃣ 🎯 오늘의 액션 가이드*
1~3단계에서 수집한 정보를 종합 분석하여 아래 내용을 제공:
🚨 *즉시 처리 필요*
> • 오늘 마감이거나 기한 초과된 태스크, 긴급 회신 필요 메일
⏰ *오늘 중 처리 권장*
> • 내일 마감 태스크, 중요 메일 회신
📌 *이번 주 내 처리*
> • 곧 마감되는 태스크, 팔로업 필요 항목
각 항목에 구체적인 행동 제안 포함 (예: "XX 메일에 YY 내용으로 회신 필요", "ZZ 태스크 상태를 In Review로 변경 권장")
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


def run_workflow():
    """Claude CLI로 워크플로우 실행 (실시간 로그)"""
    gmail_after_epoch = int(load_last_run_timestamp())
    save_last_run_timestamp()
    prompt = build_workflow_prompt(gmail_after_epoch)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    detail_log = os.path.join(LOG_DIR, f"workflow_{timestamp}.log")
    raw_log = os.path.join(LOG_DIR, f"workflow_{timestamp}.json")

    logger.info(f"워크플로우 시작 - Gmail 검색 기준: after:{gmail_after_epoch} - 실시간 로그: tail -f {detail_log}")

    cmd = [
        CLAUDE_PATH, "-p",
        "--model", "global.anthropic.claude-opus-4-6-v1",
        prompt,
        "--dangerously-skip-permissions",
        "--verbose",
        "--output-format", "stream-json",
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
