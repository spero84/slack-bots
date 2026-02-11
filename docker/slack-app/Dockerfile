# Slack App Docker Image
# Claude CLI 기반 Slack 봇 (Socket Mode)

FROM python:3.13-slim

# 기본 패키지 설치
RUN apt-get update && apt-get install -y \
    curl \
    git \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20.x 설치 (Claude-code 요구사항)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2 설치 (Bedrock 접근용)
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws

# Claude CLI 설치
RUN npm install -g @anthropic-ai/claude-code

# 작업 디렉토리 설정
WORKDIR /app

# Python 의존성 설치
COPY requirements-slack-app.txt /app/
RUN pip install --no-cache-dir -r requirements-slack-app.txt

# 소스 코드 복사
COPY src/slack_app /app/src/slack_app

# 작업 디렉토리 (Claude CLI가 사용)
RUN mkdir -p /app/workspace

# 환경변수
ENV PYTHONUNBUFFERED=1
ENV CLAUDE_WORKING_DIR=/app/workspace

# 헬스체크 (process 확인)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python.*app.py" || exit 1

# 실행
CMD ["python", "-m", "src.slack_app.app"]
