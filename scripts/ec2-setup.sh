#!/bin/bash
#
# EC2 초기 설정 스크립트 (Ubuntu 22.04/24.04)
# SSH 또는 SSM Session Manager로 접속 후 실행
#
# Docker 대신 systemd 서비스로 앱 실행
# - 모든 앱에서 Claude Code 사용 가능
# - 환경변수는 .env 파일에서 직접 로드
#
# 사용법:
#   chmod +x ec2-setup.sh
#   sudo ./ec2-setup.sh
#
set -ex

# 로그 파일 설정
LOG_FILE="/var/log/ec2-setup.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=========================================="
echo "EC2 Setup Script - $(date)"
echo "=========================================="

# 작업 디렉토리 설정
WORK_HOME="/home/ubuntu"

# ============================================
# 0. apt_pkg 복구 (이전 실행으로 깨진 경우)
# ============================================
echo "[0/7] apt_pkg 복구 확인..."
if ! python3 -c "import apt_pkg" 2>/dev/null; then
    echo "apt_pkg 모듈이 깨져있음. 복구 중..."

    # Ubuntu 24.04의 시스템 Python 버전 확인
    SYSTEM_PYTHON=""
    if [ -x /usr/bin/python3.12 ]; then
        SYSTEM_PYTHON="/usr/bin/python3.12"
    elif [ -x /usr/bin/python3.10 ]; then
        SYSTEM_PYTHON="/usr/bin/python3.10"  # Ubuntu 22.04
    fi

    if [ -n "$SYSTEM_PYTHON" ]; then
        echo "시스템 Python 발견: $SYSTEM_PYTHON"
        # python3 심볼릭 링크 복원
        ln -sf "$SYSTEM_PYTHON" /usr/bin/python3

        # python3-apt 재설치 (dpkg로 apt 훅 우회)
        apt-get download python3-apt 2>/dev/null || true
        dpkg -i --force-confold python3-apt*.deb 2>/dev/null || true
        rm -f python3-apt*.deb
    fi

    # 여전히 실패하면 cnf-update-db 훅 비활성화
    if ! python3 -c "import apt_pkg" 2>/dev/null; then
        echo "apt_pkg 여전히 깨짐. cnf-update-db 훅 비활성화..."
        chmod -x /usr/lib/cnf-update-db 2>/dev/null || true
    fi
else
    # apt_pkg가 정상이면 cnf-update-db 권한 복원 (이전 실행에서 비활성화된 경우)
    if [ -f /usr/lib/cnf-update-db ] && [ ! -x /usr/lib/cnf-update-db ]; then
        echo "cnf-update-db 권한 복원..."
        chmod +x /usr/lib/cnf-update-db 2>/dev/null || true
    fi
fi

# ============================================
# 1. 시스템 업데이트
# ============================================
echo "[1/7] 시스템 업데이트..."
apt-get update || true  # command-not-found 에러 무시
apt-get upgrade -y

# ============================================
# 2. 기본 패키지 설치
# ============================================
echo "[2/7] 기본 패키지 설치..."
# 기본 빌드 도구 + Playwright 브라우저 의존성 포함
apt-get install -y \
    curl \
    wget \
    git \
    unzip \
    build-essential \
    libssl-dev \
    libffi-dev \
    software-properties-common \
    ca-certificates \
    gnupg \
    lsb-release \
    jq \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2t64 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0

# ============================================
# 3. Python 3.13 설치
# ============================================
echo "[3/7] Python 3.13 설치..."
# Ubuntu 24.04는 Python 3.12가 기본, 3.13 추가 설치
# 주의: 시스템 Python(python3)은 변경하지 않음 (apt_pkg 호환성)
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update || true  # command-not-found 에러 무시
apt-get install -y python3.13 python3.13-venv python3.13-dev python3-pip

# python3.13을 별도로 사용 (시스템 python3는 건드리지 않음)
echo "Python 3.13 설치됨: $(python3.13 --version)"

# ============================================
# 4. uv 설치 (Python 패키지 매니저)
# ============================================
echo "[4/7] uv 설치..."
# ubuntu 사용자로 uv 설치
sudo -u ubuntu bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# 모든 사용자를 위한 PATH 설정
cat > /etc/profile.d/uv.sh << 'EOF'
export PATH="$HOME/.local/bin:$PATH"
EOF

# ============================================
# 5. Node.js 22.x 및 Claude Code 설치
# ============================================
echo "[5/7] Node.js 22.x 및 Claude Code 설치..."
# NodeSource 스크립트 실행 (apt update 에러에 민감하므로 실패 대비)
curl -fsSL https://deb.nodesource.com/setup_22.x | bash - || {
    echo "NodeSource 스크립트 실패, 수동 설치 시도..."
    # keyrings 디렉토리 생성
    mkdir -p /etc/apt/keyrings
    # NodeSource GPG 키 추가
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
    # NodeSource repository 추가
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list
    apt-get update || true
}
apt-get install -y nodejs

# Claude Code 설치 (ubuntu 사용자로 실행)
sudo -u ubuntu bash -c 'curl -fsSL https://claude.ai/install.sh | bash'

# 버전 확인
echo "Node.js version: $(node --version)"
echo "npm version: $(npm --version)"
echo "Claude Code version: $(sudo -u ubuntu bash -c 'claude --version' 2>/dev/null || echo 'installed')"

# ============================================
# 6. AWS CLI v2 설치
# ============================================
echo "[6/7] AWS CLI v2 설치..."
if ! command -v aws &> /dev/null; then
    ARCH=$(uname -m)
    if [ "$ARCH" = "aarch64" ]; then
        AWS_CLI_URL="https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip"
    else
        AWS_CLI_URL="https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
    fi
    curl "$AWS_CLI_URL" -o "/tmp/awscliv2.zip"
    unzip -o /tmp/awscliv2.zip -d /tmp
    /tmp/aws/install
    rm -rf /tmp/awscliv2.zip /tmp/aws
fi

echo "AWS CLI version: $(aws --version)"

# ============================================
# 7. 작업 디렉토리 및 스크립트 생성
# ============================================
echo "[7/7] 작업 디렉토리, systemd 서비스 및 스크립트 생성..."

# 작업 디렉토리 생성
mkdir -p "$WORK_HOME"/{slack-bots,venvs,logs}
mkdir -p "$WORK_HOME/slack-bots/workspace"

# ============================================
# systemd 서비스 파일 생성
# ============================================

# slack-app.service
cat > /etc/systemd/system/slack-app.service << 'EOF'
[Unit]
Description=Slack App (Socket Mode Bot)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/slack-bots
EnvironmentFile=/home/ubuntu/slack-bots/.env
Environment=PYTHONUNBUFFERED=1
Environment=CLAUDE_WORKING_DIR=/home/ubuntu/slack-bots/workspace
ExecStart=/home/ubuntu/venvs/slack-app/bin/python -m src.slack_app.app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# scheduler.service
cat > /etc/systemd/system/scheduler.service << 'EOF'
[Unit]
Description=Scheduler (Workflow Automation)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/slack-bots
EnvironmentFile=/home/ubuntu/slack-bots/.env
Environment=PYTHONUNBUFFERED=1
Environment=CLAUDE_WORKING_DIR=/home/ubuntu/slack-bots/workspace
Environment=PATH=/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/home/ubuntu/venvs/scheduler/bin/python -m src.scheduler.scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# gov-funding.service
cat > /etc/systemd/system/gov-funding.service << 'EOF'
[Unit]
Description=Gov Funding Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/slack-bots
EnvironmentFile=/home/ubuntu/slack-bots/.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/venvs/gov-funding/bin/python -m src.gov_funding.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# systemd 데몬 리로드
systemctl daemon-reload

# 서비스 활성화 (부팅 시 자동 시작)
systemctl enable slack-app scheduler gov-funding

# ============================================
# 운영 스크립트 심볼릭 링크 생성
# ============================================
SCRIPTS=(
    download-source.sh
    setup-venvs.sh
    start-all.sh
    start-slack-app.sh
    start-scheduler.sh
    start-gov-funding.sh
    stop-all.sh
    restart-all.sh
    status.sh
    logs.sh
)

for script in "${SCRIPTS[@]}"; do
    ln -sf "$WORK_HOME/slack-bots/scripts/$script" "$WORK_HOME/$script"
done

# 소유권 설정
chown -R ubuntu:ubuntu "$WORK_HOME"

# ============================================
# 완료
# ============================================
echo ""
echo "=========================================="
echo "EC2 Setup 완료!"
echo "=========================================="
echo ""
echo "설치된 버전:"
echo "  - Python: $(python3 --version)"
echo "  - Python 3.13: $(python3.13 --version)"
echo "  - Node.js: $(node --version)"
echo "  - AWS CLI: $(aws --version)"
echo "  - Claude Code: $(sudo -u ubuntu bash -c 'claude --version' 2>/dev/null || echo 'installed')"
echo ""
echo "Systemd 서비스:"
echo "  - slack-app.service"
echo "  - scheduler.service"
echo "  - gov-funding.service"
echo ""
echo "다음 단계:"
echo "  1. /home/ubuntu/download-source.sh  # 소스 다운로드"
echo "  2. /home/ubuntu/setup-venvs.sh      # Python venv 설정"
echo "  3. /home/ubuntu/start-all.sh        # 서비스 시작"
echo ""
echo "운영 명령어:"
echo "  /home/ubuntu/status.sh              # 상태 확인"
echo "  /home/ubuntu/logs.sh slack-app      # 로그 확인"
echo "  /home/ubuntu/restart-all.sh         # 재시작"
echo ""
echo "로그 파일: $LOG_FILE"
