#!/bin/bash
echo "=== AI News 서비스 시작 ==="
sudo systemctl start ai-news
echo ""
systemctl status ai-news --no-pager
echo ""
echo "로그 확인: journalctl -u ai-news -f"
