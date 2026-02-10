#!/bin/bash
set -e

echo "=== 소스 다운로드 ==="

# 서비스 중지 (실행 중인 경우)
sudo systemctl stop slack-app scheduler gov-funding 2>/dev/null || true

# 소스 다운로드
cd /home/ubuntu
rm -rf slack-bots
mkdir -p slack-bots
aws s3 cp s3://slack-bots-prod-snapshots/source/slack-bots-source.zip /tmp/slack-bots-source.zip --region ap-northeast-2
unzip -o /tmp/slack-bots-source.zip -d slack-bots
rm /tmp/slack-bots-source.zip

# .env 파일 다운로드 (있으면)
aws s3 cp s3://slack-bots-prod-snapshots/source/.env /home/ubuntu/slack-bots/.env --region ap-northeast-2 || echo ".env 파일 없음 - 수동 생성 필요"

# workspace 디렉토리 생성
mkdir -p /home/ubuntu/slack-bots/workspace

echo "소스 다운로드 완료: /home/ubuntu/slack-bots"
echo ""
echo "다음 단계:"
echo "  1. venv 업데이트 (의존성 변경 시): /home/ubuntu/setup-venvs.sh"
echo "  2. 서비스 시작: /home/ubuntu/start-all.sh"
