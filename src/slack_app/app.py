"""Slack App - Socket Mode 기반 Claude CLI 봇"""

import json
import logging
import os
import re
import subprocess
import time
import uuid

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# .env 파일에서 환경변수 로드
load_dotenv(override=True)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))

# 세션 저장소
user_sessions = {}    # DM: 사용자별 세션 {user_id: session_uuid}
thread_sessions = {}  # 채널: 스레드별 세션 {(channel_id, thread_ts): session_uuid}
active_sessions = set()  # 이미 시작된 세션 추적

# 작업 디렉토리 (환경변수로 설정 가능)
HOME_DIR = os.environ.get("HOME", "/home/ubuntu")
WORKING_DIR = os.environ.get("CLAUDE_WORKING_DIR", "/app/workspace")

# Claude CLI 경로 (systemd 환경에서 PATH에 없을 수 있음)
CLAUDE_PATH = os.path.join(HOME_DIR, ".local", "bin", "claude")

# Gov-Funding Q&A 설정
GOV_FUNDING_CHANNEL_ID = os.environ.get("GOV_FUNDING_CHANNEL_ID", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")

# Gov-Funding 스냅샷 캐시 (TTL 1시간)
_gov_funding_cache = {"data": None, "fetched_at": 0, "ttl": 3600}


def get_gov_funding_context():
    """S3에서 최신 gov-funding 스냅샷을 가져와 컨텍스트 문자열로 반환 (1시간 캐시)"""
    now = time.time()

    # 캐시 히트
    if _gov_funding_cache["data"] is not None and (now - _gov_funding_cache["fetched_at"]) < _gov_funding_cache["ttl"]:
        logger.info("Gov-funding 캐시 히트")
        return _gov_funding_cache["data"]

    if not S3_BUCKET:
        logger.warning("S3_BUCKET 미설정 - gov-funding 컨텍스트 스킵")
        return ""

    logger.info("Gov-funding S3 스냅샷 fetch 시작")
    s3_client = boto3.client("s3", region_name=AWS_REGION)
    all_announcements = []

    for source in ["kstartup", "bizinfo", "nipa"]:
        prefix = f"snapshots/{source}/"
        try:
            response = s3_client.list_objects_v2(
                Bucket=S3_BUCKET, Prefix=prefix, MaxKeys=1000
            )
            if "Contents" not in response:
                logger.info(f"{source} 스냅샷 없음")
                continue

            # 최신 파일 선택
            objects = sorted(response["Contents"], key=lambda x: x["LastModified"], reverse=True)
            latest_key = objects[0]["Key"]

            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=latest_key)
            data = json.loads(obj["Body"].read().decode("utf-8"))
            announcements = data.get("announcements", [])
            all_announcements.extend(announcements)
            logger.info(f"{source} 스냅샷 로드: {len(announcements)}건 ({latest_key})")
        except ClientError as e:
            logger.error(f"{source} S3 접근 에러: {e}")
            continue
        except Exception as e:
            logger.error(f"{source} 스냅샷 파싱 에러: {e}")
            continue

    context = _format_announcements_context(all_announcements)

    # 캐시 저장 (데이터 없으면 5분 TTL)
    _gov_funding_cache["data"] = context
    _gov_funding_cache["fetched_at"] = now
    if not all_announcements:
        _gov_funding_cache["ttl"] = 300
    else:
        _gov_funding_cache["ttl"] = 3600

    return context


def _format_announcements_context(announcements):
    """공고 리스트를 텍스트 컨텍스트로 포맷팅"""
    if not announcements:
        return ""

    lines = []
    for i, ann in enumerate(announcements, 1):
        parts = [f"[{i}] {ann.get('title', '제목 없음')}"]
        if ann.get("source"):
            parts.append(f"  출처: {ann['source']}")
        if ann.get("category"):
            parts.append(f"  분야: {ann['category']}")
        if ann.get("d_day") is not None:
            parts.append(f"  D-day: D-{ann['d_day']}")
        if ann.get("organization"):
            parts.append(f"  주관기관: {ann['organization']}")
        if ann.get("department"):
            parts.append(f"  소관부처: {ann['department']}")
        if ann.get("summary"):
            parts.append(f"  요약: {ann['summary']}")
        if ann.get("url"):
            parts.append(f"  URL: {ann['url']}")
        if ann.get("relevance_score") is not None:
            parts.append(f"  관련성 점수: {ann['relevance_score']}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def _build_gov_funding_prompt(question, context):
    """gov-funding 채널용 프롬프트 구성"""
    if not context:
        return (
            f"사용자 질문: {question}\n\n"
            "현재 정부 지원사업 공고 데이터가 없습니다. "
            "데이터가 아직 수집되지 않았을 수 있습니다.\n"
            "직접 확인할 수 있는 사이트:\n"
            "- K-Startup: https://www.k-startup.go.kr\n"
            "- 기업마당: https://www.bizinfo.go.kr\n"
            "- NIPA: https://www.nipa.kr\n\n"
            "위 안내와 함께 질문에 최대한 답변해 주세요."
        )

    return (
        "당신은 정부 지원사업 전문 어시스턴트입니다. "
        "아래 공고 데이터를 기반으로 사용자의 질문에 답변하세요.\n\n"
        "규칙:\n"
        "- 데이터에 있는 정보만 기반으로 답변하세요\n"
        "- 관련 공고의 URL을 반드시 포함하세요\n"
        "- 마감 임박(D-7 이내) 공고는 강조해서 알려주세요\n"
        "- 관련성 점수가 높은 공고를 우선 추천하세요\n"
        "- Slack 메시지로 출력되므로 Slack mrkdwn 형식을 사용하세요\n\n"
        f"=== 현재 정부 지원사업 공고 데이터 ({len(context.split('['))-1}건) ===\n\n"
        f"{context}\n\n"
        f"=== 사용자 질문 ===\n{question}"
    )


def get_user_session(user_id):
    """DM용: 사용자별 세션"""
    is_new = user_id not in user_sessions
    if is_new:
        user_sessions[user_id] = str(uuid.uuid4())
    return user_sessions[user_id], is_new


def get_thread_session(channel_id, thread_ts):
    """채널용: 스레드별 세션"""
    key = (channel_id, thread_ts)
    is_new = key not in thread_sessions
    if is_new:
        thread_sessions[key] = str(uuid.uuid4())
    return thread_sessions[key], is_new


def call_claude(prompt, session_id, is_new_session):
    """Claude CLI를 호출하여 응답을 반환"""
    try:
        # 새 세션이면 --session-id, 기존 세션이면 --resume 사용
        if is_new_session and session_id not in active_sessions:
            cmd = [CLAUDE_PATH, "-p", "--model", "opus",
                   "--session-id", session_id,
                   "--output-format", "text", prompt]
        else:
            cmd = [CLAUDE_PATH, "-p", "--model", "opus",
                   "--resume", session_id,
                   "--output-format", "text", prompt]

        logger.info(f"Claude 호출 - session: {session_id}, new: {is_new_session}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=WORKING_DIR
        )

        # 세션 성공 시 활성 세션으로 등록
        if result.returncode == 0:
            active_sessions.add(session_id)
            return result.stdout.strip()
        else:
            logger.error(f"Claude 에러 - returncode: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}")
            return f"에러: {result.stdout or result.stderr}"
    except subprocess.TimeoutExpired:
        logger.error(f"Claude 타임아웃 - session: {session_id}")
        return "에러: Claude 응답 시간이 초과되었습니다 (2분)"
    except Exception as e:
        logger.error(f"Claude 예외 - session: {session_id}, error: {e}")
        return f"에러: {str(e)}"


@app.event("message")
def handle_message(event, say):
    """DM 및 채널 메시지 처리"""
    # 봇 자신의 메시지는 무시
    if event.get("bot_id"):
        return

    user = event.get("user")
    text = event.get("text", "")
    channel_type = event.get("channel_type")

    # DM인 경우 - 사용자별 세션
    if channel_type == "im":
        logger.info(f"DM 수신 - User: {user}, Text: {text}")
        # 처리 중 메시지 표시
        say("처리 중...")
        # 사용자별 세션으로 Claude CLI 호출
        session_id, is_new = get_user_session(user)
        response = call_claude(text, session_id, is_new)
        say(response)


@app.event("app_mention")
def handle_mention(event, say):
    """봇 멘션 처리 - 스레드별 세션"""
    user = event.get("user")
    text = event.get("text", "")
    channel = event.get("channel")
    # 스레드가 없으면 메시지 ts를 사용 (새 스레드 시작점)
    thread_ts = event.get("thread_ts") or event.get("ts")

    logger.info(f"멘션 수신 - User: {user}, Channel: {channel}, Thread: {thread_ts}, Text: {text}")
    # 봇 멘션 태그 제거 (<@BOT_ID> 형식)
    clean_text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

    if not clean_text:
        say(f"<@{user}> 무엇을 도와드릴까요?")
        return

    # 처리 중 메시지 표시
    say(f"<@{user}> 처리 중...")
    # Gov-Funding 채널이면 S3 스냅샷 컨텍스트 주입
    if GOV_FUNDING_CHANNEL_ID and channel == GOV_FUNDING_CHANNEL_ID:
        context = get_gov_funding_context()
        prompt = _build_gov_funding_prompt(clean_text, context)
    else:
        prompt = clean_text

    # 스레드별 세션으로 Claude CLI 호출
    session_id, is_new = get_thread_session(channel, thread_ts)
    response = call_claude(prompt, session_id, is_new)
    say(f"<@{user}> {response}")


def main():
    """Socket Mode로 앱 실행"""
    handler = SocketModeHandler(
        app,
        os.environ.get("SLACK_APP_TOKEN")
    )
    logger.info("Bot is running...")
    handler.start()


if __name__ == "__main__":
    main()
