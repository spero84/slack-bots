#!/bin/bash
SERVICE=${1:-slack-app}
echo "=== $SERVICE 로그 (Ctrl+C로 종료) ==="
journalctl -u "$SERVICE" -f
