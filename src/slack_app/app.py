"""Slack App - Socket Mode 기반 Claude CLI 봇"""

import logging
import os
import re
import subprocess
import uuid

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
            cmd = [CLAUDE_PATH, "-p", "--session-id", session_id,
                   "--output-format", "text", prompt]
        else:
            cmd = [CLAUDE_PATH, "-p", "--resume", session_id,
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
    # 스레드별 세션으로 Claude CLI 호출
    session_id, is_new = get_thread_session(channel, thread_ts)
    response = call_claude(clean_text, session_id, is_new)
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
