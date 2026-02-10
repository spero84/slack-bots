#!/bin/bash
echo "=== slack-app 시작 ==="
sudo systemctl start slack-app
systemctl status slack-app --no-pager
