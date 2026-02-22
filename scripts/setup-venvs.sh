#!/bin/bash
set -e

# uv PATH 설정
export PATH="$HOME/.local/bin:$PATH"

echo "=== Python 가상환경 설정 (uv) ==="
cd /home/ubuntu/slack-bots

# slack-app venv
echo "[1/4] slack-app venv 설정..."
rm -rf /home/ubuntu/venvs/slack-app
uv venv /home/ubuntu/venvs/slack-app --python python3.13
uv pip install -r requirements-slack-app.txt --python /home/ubuntu/venvs/slack-app/bin/python

# scheduler venv
echo "[2/4] scheduler venv 설정..."
rm -rf /home/ubuntu/venvs/scheduler
uv venv /home/ubuntu/venvs/scheduler --python python3.13
uv pip install -r requirements.txt --python /home/ubuntu/venvs/scheduler/bin/python
uv pip install apscheduler slack_sdk --python /home/ubuntu/venvs/scheduler/bin/python

# gov-funding venv
echo "[3/4] gov-funding venv 설정..."
rm -rf /home/ubuntu/venvs/gov-funding
uv venv /home/ubuntu/venvs/gov-funding --python python3.13
uv pip install -r requirements-gov-funding.txt --python /home/ubuntu/venvs/gov-funding/bin/python
uv pip install olefile --python /home/ubuntu/venvs/gov-funding/bin/python
uv pip install playwright --python /home/ubuntu/venvs/gov-funding/bin/python
/home/ubuntu/venvs/gov-funding/bin/playwright install chromium

# ai-news venv
echo "[4/4] ai-news venv 설정..."
rm -rf /home/ubuntu/venvs/ai-news
uv venv /home/ubuntu/venvs/ai-news --python python3.13
uv pip install -r requirements-ai-news.txt --python /home/ubuntu/venvs/ai-news/bin/python

echo ""
echo "=== 가상환경 설정 완료 ==="
echo "  - /home/ubuntu/venvs/slack-app"
echo "  - /home/ubuntu/venvs/scheduler"
echo "  - /home/ubuntu/venvs/gov-funding"
echo "  - /home/ubuntu/venvs/ai-news"
