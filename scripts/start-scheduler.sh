#!/bin/bash
echo "=== scheduler 시작 ==="
sudo systemctl start scheduler
systemctl status scheduler --no-pager
