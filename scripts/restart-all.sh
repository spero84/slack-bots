#!/bin/bash
echo "=== 전체 서비스 재시작 ==="
sudo systemctl restart slack-app
sudo systemctl restart scheduler
sudo systemctl restart gov-funding
sudo systemctl restart ai-news
echo ""
echo "=== 상태 확인 ==="
systemctl status slack-app scheduler gov-funding ai-news --no-pager
