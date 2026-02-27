#!/bin/bash
INPUT=$(cat)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path')
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active')

# 재평가 시 무한루프 방지
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  exit 0
fi

# transcript 파일 없으면 통과
if [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

# src/ 파일 수정 여부 확인 (Edit/Write 도구 사용 기록)
if grep -q '"file_path".*".*src/\(slack_app\|scheduler\|gov_funding\|ai_news\)/' "$TRANSCRIPT_PATH" 2>/dev/null; then
  # systemctl restart 실행 여부 확인
  if ! grep -q 'systemctl restart' "$TRANSCRIPT_PATH" 2>/dev/null; then
    cat <<'WARN'
{"decision":"block","reason":"src/ 코드가 수정되었지만 서비스가 재시작되지 않았습니다. 변경된 서비스를 재시작하세요: sudo systemctl restart <service>\n(src/slack_app/ → slack-app, src/scheduler/ → scheduler, src/gov_funding/ → gov-funding, src/ai_news/ → ai-news)"}
WARN
    exit 0
  fi
fi

exit 0
