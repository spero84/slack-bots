FROM python:3.13-slim

WORKDIR /app

# 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성
COPY requirements-ai-news.txt .
RUN pip install --no-cache-dir -r requirements-ai-news.txt

# 소스 코드
COPY src/ src/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.ai_news.main"]
