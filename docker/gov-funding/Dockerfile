# Gov Funding Monitor Docker Image
# 정부 지원사업 공고 모니터링 (APScheduler 기반)

FROM python:3.13-slim

# 기본 패키지 설치 (Playwright 의존성 포함)
RUN apt-get update && apt-get install -y \
    curl \
    git \
    unzip \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2 설치 (S3, Bedrock 접근용)
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws

# 작업 디렉토리 설정
WORKDIR /app

# Python 의존성 설치
COPY requirements-gov-funding.txt /app/
RUN pip install --no-cache-dir -r requirements-gov-funding.txt

# Playwright 브라우저 설치
RUN playwright install chromium

# 소스 코드 복사
COPY src/gov_funding /app/src/gov_funding

# 환경변수
ENV PYTHONUNBUFFERED=1

# 헬스체크 (프로세스 확인)
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD pgrep -f "python.*gov_funding" || exit 1

# 실행
CMD ["python", "-m", "src.gov_funding.main"]
