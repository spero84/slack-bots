#!/bin/bash
# Playwright Lambda Layer 빌드 스크립트
# Lambda에서 Playwright를 실행하기 위한 레이어 생성

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LAYER_DIR="$PROJECT_DIR/layers/playwright"

echo "🔧 Playwright Lambda Layer 빌드 시작..."

# 임시 디렉토리 생성
TEMP_DIR=$(mktemp -d)
echo "📁 임시 디렉토리: $TEMP_DIR"

# Python 패키지 설치
echo "📦 Python 패키지 설치 중..."
pip install \
    playwright==1.40.0 \
    --platform manylinux2014_x86_64 \
    --only-binary=:all: \
    --target "$TEMP_DIR/python" \
    -q

# Playwright 브라우저 다운로드 (Chromium만)
echo "🌐 Chromium 브라우저 다운로드 중..."
PLAYWRIGHT_BROWSERS_PATH="$TEMP_DIR/python/playwright/driver/package/.local-browsers"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH"

# 참고: 실제 Lambda 환경에서는 playwright-chromium Layer를 별도로 사용하거나
# Amazon Linux 2 기반 컨테이너에서 빌드해야 합니다.
# 아래는 Mac에서 빌드하는 경우의 스크립트입니다.

# Lambda 호환 Chromium 다운로드 (외부 Layer 사용 권장)
# https://github.com/nicholasbs/chromium-lambda-layer
echo "⚠️  Lambda용 Chromium은 별도 Layer를 사용하거나 Docker에서 빌드하세요"
echo "   참고: https://playwright.dev/python/docs/browsers#install-system-dependencies"

# 레이어 ZIP 생성
echo "📦 Layer ZIP 생성 중..."
cd "$TEMP_DIR"
zip -r9 playwright-layer.zip python/ > /dev/null

# 결과 복사
mkdir -p "$LAYER_DIR"
mv playwright-layer.zip "$LAYER_DIR/"

# 정리
rm -rf "$TEMP_DIR"

echo "✅ Layer 빌드 완료: $LAYER_DIR/playwright-layer.zip"
echo ""
echo "📝 다음 단계:"
echo "   1. Lambda Layer로 업로드하거나 Terraform으로 배포"
echo "   2. 또는 Docker 기반 Lambda 사용 고려"
echo ""
echo "💡 권장: Lambda Container Image 사용"
echo "   - Playwright + Chromium이 포함된 Docker 이미지 사용"
echo "   - https://playwright.dev/python/docs/docker"
