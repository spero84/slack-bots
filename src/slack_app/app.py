"""Slack App - Socket Mode 기반 Claude CLI 봇"""

import json
import logging
import os
import re
import subprocess
import uuid

import boto3
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
S3_VECTOR_BUCKET = os.environ.get("S3_BUCKET", "gov-funding-monitor-snapshots")
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-west-2")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")

# S3 Vectors / Bedrock 클라이언트 (lazy init)
_s3v_client = None
_bedrock_embed_client = None


def _get_s3v_client():
    global _s3v_client
    if _s3v_client is None:
        _s3v_client = boto3.client("s3vectors", region_name=AWS_REGION)
    return _s3v_client


def _get_bedrock_embed_client():
    global _bedrock_embed_client
    if _bedrock_embed_client is None:
        _bedrock_embed_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_embed_client


def get_gov_funding_context(question):
    """사용자 질문으로 S3 Vectors 검색 → 관련 공고 컨텍스트 반환"""
    if not S3_VECTOR_BUCKET:
        logger.warning("S3_VECTOR_BUCKET 미설정 - gov-funding 컨텍스트 스킵")
        return ""

    try:
        # 1. 질문 임베딩
        logger.info(f"Gov-funding 벡터 검색 시작: {question[:50]}")
        bedrock = _get_bedrock_embed_client()
        resp = bedrock.invoke_model(
            modelId=EMBEDDING_MODEL,
            body=json.dumps({"inputText": question, "dimensions": 1024}),
        )
        embedding = json.loads(resp["body"].read())["embedding"]

        # 2. 메타데이터 필터 구성 (키워드 검색)
        metadata_filter = _extract_metadata_filter(question)

        # 3. 벡터 유사도 검색
        s3v = _get_s3v_client()
        kwargs = {
            "vectorBucketName": S3_VECTOR_BUCKET,
            "indexName": "announcements",
            "queryVector": {"float32": embedding},
            "topK": 15,
            "returnMetadata": True,
            "returnDistance": True,
        }
        if metadata_filter:
            kwargs["filter"] = metadata_filter

        results = s3v.query_vectors(**kwargs)
        vectors = results.get("vectors", [])
        logger.info(f"벡터 검색 결과: {len(vectors)}건 (필터: {metadata_filter})")

        # 4. 결과가 적으면 필터 없이 재검색
        if len(vectors) < 3 and metadata_filter:
            logger.info("결과 부족 - 필터 없이 재검색")
            kwargs.pop("filter", None)
            results = s3v.query_vectors(**kwargs)
            vectors = results.get("vectors", [])
            logger.info(f"재검색 결과: {len(vectors)}건")

        return _format_vector_results(vectors)

    except Exception as e:
        logger.error(f"S3 Vectors 검색 에러: {e}")
        return ""


def _extract_metadata_filter(question):
    """질문에서 메타데이터 필터 추출 (키워드 검색)"""
    filters = []
    q = question.lower()

    # 소스 필터
    source_map = {
        "nipa": "nipa", "니파": "nipa",
        "kstartup": "kstartup", "k-startup": "kstartup", "케이스타트업": "kstartup",
        "기업마당": "bizinfo", "bizinfo": "bizinfo",
    }
    for keyword, source in source_map.items():
        if keyword in q:
            filters.append({"source": source})
            break

    # D-day 필터 (마감임박)
    if any(kw in q for kw in ["마감", "임박", "긴급", "d-day"]):
        filters.append({"d_day": {"$gte": 0, "$lte": 7}})

    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    return {"$and": filters}


def _format_vector_results(vectors):
    """벡터 검색 결과를 텍스트 컨텍스트로 포맷팅"""
    if not vectors:
        return ""

    lines = []
    for i, v in enumerate(vectors, 1):
        meta = v.get("metadata", {})
        distance = v.get("distance")
        parts = [f"[{i}] {meta.get('title', '제목 없음')}"]
        if meta.get("source"):
            parts.append(f"  출처: {meta['source']}")
        if meta.get("category"):
            parts.append(f"  분야: {meta['category']}")
        d_day = meta.get("d_day")
        if d_day is not None and d_day >= 0:
            parts.append(f"  D-day: D-{d_day}")
        if meta.get("organization"):
            parts.append(f"  주관기관: {meta['organization']}")
        if meta.get("department"):
            parts.append(f"  소관부처: {meta['department']}")
        if meta.get("summary"):
            parts.append(f"  요약: {meta['summary']}")
        if meta.get("url"):
            parts.append(f"  URL: {meta['url']}")
        if meta.get("relevance_score") is not None:
            parts.append(f"  관련성 점수: {meta['relevance_score']}")
        if distance is not None:
            parts.append(f"  유사도: {1 - distance:.3f}")
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

    count = context.count("\n[")
    if context.startswith("["):
        count += 1
    return (
        "당신은 정부 지원사업 전문 어시스턴트입니다. "
        "아래는 사용자 질문과 가장 관련성 높은 공고 데이터입니다 (벡터 유사도 검색 결과).\n\n"
        "규칙:\n"
        "- 데이터에 있는 정보만 기반으로 답변하세요\n"
        "- 관련 공고의 URL을 반드시 포함하세요\n"
        "- 마감 임박(D-7 이내) 공고는 강조해서 알려주세요\n"
        "- 유사도가 높은 공고를 우선 추천하세요\n"
        "- Slack 메시지로 출력되므로 Slack mrkdwn 형식을 사용하세요\n\n"
        f"=== 검색된 정부 지원사업 공고 ({count}건) ===\n\n"
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
            cmd = [CLAUDE_PATH, "-p", "--model", "global.anthropic.claude-opus-4-6-v1",
                   "--max-turns", "1",
                   "--session-id", session_id,
                   "--output-format", "text", prompt]
        else:
            cmd = [CLAUDE_PATH, "-p", "--model", "global.anthropic.claude-opus-4-6-v1",
                   "--max-turns", "1",
                   "--resume", session_id,
                   "--output-format", "text", prompt]

        logger.info(f"Claude 호출 - session: {session_id}, new: {is_new_session}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
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
        return "에러: Claude 응답 시간이 초과되었습니다 (5분)"
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
        say(f"<@{user}> 무엇을 도와드릴까요?", thread_ts=thread_ts)
        return

    # 처리 중 메시지 표시
    say(f"<@{user}> 처리 중...", thread_ts=thread_ts)
    # Gov-Funding 채널이면 S3 Vectors 벡터 검색으로 관련 공고 컨텍스트 주입
    if GOV_FUNDING_CHANNEL_ID and channel == GOV_FUNDING_CHANNEL_ID:
        context = get_gov_funding_context(clean_text)
        prompt = _build_gov_funding_prompt(clean_text, context)
    else:
        prompt = clean_text

    # 스레드별 세션으로 Claude CLI 호출
    session_id, is_new = get_thread_session(channel, thread_ts)
    response = call_claude(prompt, session_id, is_new)
    say(f"<@{user}> {response}", thread_ts=thread_ts)


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
