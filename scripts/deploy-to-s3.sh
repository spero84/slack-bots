#!/bin/bash
#
# 소스 코드를 S3에 배포
# zip 압축 후 s3://slack-bots-prod-snapshots/source/slack-bots-source.zip 업로드
#
# 사용법:
#   ./scripts/deploy-to-s3.sh
#
set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Slack Bots S3 배포 ===${NC}"

# 프로젝트 루트로 이동
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "프로젝트 루트: $PROJECT_ROOT"

# S3 버킷 및 경로
S3_BUCKET="slack-bots-prod-snapshots"
S3_KEY="source/slack-bots-source.zip"
S3_PATH="s3://$S3_BUCKET/$S3_KEY"

# 임시 zip 파일
TMP_ZIP="/tmp/slack-bots-source.zip"

# 기존 zip 파일 삭제
rm -f "$TMP_ZIP"

echo -e "${YELLOW}소스 파일 압축 중...${NC}"

# 필수 파일들만 zip으로 압축
# - src/ : 소스 코드
# - docker/ : Dockerfile들
# - scripts/ : 스크립트
# - requirements*.txt : 의존성 파일들
# - docker-compose.yml : Docker Compose 설정
zip -r "$TMP_ZIP" \
    src/ \
    docker/ \
    scripts/ \
    requirements*.txt \
    docker-compose.yml \
    -x "*.pyc" \
    -x "*__pycache__*" \
    -x "*.egg-info*" \
    -x ".git/*" \
    -x ".venv/*" \
    -x "*.pytest_cache*" \
    -x "*.mypy_cache*"

# zip 파일 크기 확인
ZIP_SIZE=$(du -h "$TMP_ZIP" | cut -f1)
echo "압축 파일 크기: $ZIP_SIZE"

echo -e "${YELLOW}S3 업로드 중...${NC}"
echo "대상: $S3_PATH"

# S3 업로드
aws s3 cp "$TMP_ZIP" "$S3_PATH" --region ap-northeast-2

# .env 파일 업로드 (존재하는 경우)
if [ -f ".env" ]; then
    echo -e "${YELLOW}S3에 .env 파일 업로드...${NC}"
    aws s3 cp .env "s3://${S3_BUCKET}/source/.env" --region ap-northeast-2 --sse AES256
    echo ".env 파일 업로드 완료"
fi

# 업로드 확인
echo -e "${YELLOW}업로드 확인...${NC}"
aws s3 ls "$S3_PATH" --region ap-northeast-2

# 임시 파일 정리
rm -f "$TMP_ZIP"

echo ""
echo -e "${GREEN}=== 배포 완료! ===${NC}"
echo ""
echo "EC2에서 다운로드:"
echo "  /home/ssm-user/download-source.sh"
echo ""
echo "또는 수동으로:"
echo "  aws s3 cp $S3_PATH /tmp/slack-bots-source.zip --region ap-northeast-2"
echo "  unzip -o /tmp/slack-bots-source.zip -d /home/ssm-user/slack-bots"
