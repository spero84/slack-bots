#!/bin/bash
echo "=== gov-funding 시작 ==="
sudo systemctl start gov-funding
systemctl status gov-funding --no-pager
