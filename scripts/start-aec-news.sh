#!/bin/bash
echo "=== AEC News 서비스 시작 ==="
sudo systemctl start aec-news
echo ""
systemctl status aec-news --no-pager
echo ""
echo "로그 확인: journalctl -u aec-news -f"
