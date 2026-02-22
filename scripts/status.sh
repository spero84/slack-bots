#!/bin/bash
echo "=== 서비스 상태 ==="
systemctl status slack-app scheduler gov-funding ai-news --no-pager
