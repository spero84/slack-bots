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

# git commit 실행 여부 확인
if grep -q 'git commit' "$TRANSCRIPT_PATH" 2>/dev/null; then
  # git push 실행 여부 확인
  if ! grep -q 'git push' "$TRANSCRIPT_PATH" 2>/dev/null; then
    cat <<'WARN'
{"decision":"block","reason":"git commit이 수행되었지만 git push가 되지 않았습니다. 반드시 git pull → git push를 수행하세요."}
WARN
    exit 0
  fi
fi

exit 0
