#!/bin/bash
echo "=== 전체 서비스 중지 ==="
sudo systemctl stop slack-app
sudo systemctl stop scheduler
sudo systemctl stop gov-funding
sudo systemctl stop ai-news
echo "=== 완료 ==="
