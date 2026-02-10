#!/bin/bash
echo "=== 전체 서비스 시작 ==="
sudo systemctl start slack-app
sudo systemctl start scheduler
sudo systemctl start gov-funding
echo ""
echo "=== 상태 확인 ==="
systemctl status slack-app scheduler gov-funding --no-pager
echo ""
echo "로그 확인:"
echo "  journalctl -u slack-app -f"
echo "  journalctl -u scheduler -f"
echo "  journalctl -u gov-funding -f"
