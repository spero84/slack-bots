#!/bin/bash
set -e

echo "=== 소스 업데이트 ==="

# 서비스 중지 (실행 중인 경우)
sudo systemctl stop slack-app scheduler gov-funding 2>/dev/null || true

# Git pull로 최신 소스 가져오기
cd /home/ubuntu/slack-bots
git pull origin main

# workspace 디렉토리 생성
mkdir -p /home/ubuntu/slack-bots/workspace

echo "소스 업데이트 완료: /home/ubuntu/slack-bots"
echo ""
echo "다음 단계:"
echo "  1. venv 업데이트 (의존성 변경 시): /home/ubuntu/setup-venvs.sh"
echo "  2. 서비스 시작: /home/ubuntu/start-all.sh"
