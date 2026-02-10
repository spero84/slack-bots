# Slack Bots

Searchdoc Slack 봇 통합 프로젝트 - Gov Funding Monitor, Scheduler, Slack App을 AWS EC2에 Docker로 배포합니다.

## 서비스 구성

| 서비스 | 컨테이너 | 스케줄 | 설명 |
|--------|----------|--------|------|
| Slack App | `slack-app` | 상시 실행 (Socket Mode) | Claude CLI 기반 Slack 봇 (Gov-Funding Q&A 포함) |
| Scheduler | `scheduler` | 평일 9,11,13,15,17시 KST | Claude CLI 기반 업무 자동화 |
| Gov-Funding | `gov-funding` | 매일 09:00 KST | 정부 지원사업 공고 모니터링 |

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                          EC2 Instance                           │
│                        (Ubuntu 22.04+)                          │
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │   slack-app     │  │   scheduler     │  │   gov-funding   │  │
│  │   (Docker)      │  │   (Docker)      │  │   (Docker)      │  │
│  │                 │  │                 │  │                 │  │
│  │  Socket Mode    │  │  APScheduler    │  │  APScheduler    │  │
│  │  Slack Bolt     │  │  Claude CLI     │  │  Playwright     │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
│           │                    │                    │           │
└───────────┼────────────────────┼────────────────────┼───────────┘
            │                    │                    │
            ▼                    ▼                    ▼
     ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
     │  Slack API   │    │  Slack API   │    │   Bedrock    │
     │              │    │              │    │   S3 Bucket  │
     └──────────────┘    └──────────────┘    └──────────────┘
```

## 사전 요구사항

### AWS 리소스

| 리소스 | 요구사항 |
|--------|----------|
| EC2 인스턴스 | Ubuntu 22.04/24.04, t3.medium 이상 권장 |
| IAM Role | EC2에 연결, 아래 권한 필요 |
| Secrets Manager | Slack 토큰 저장 |
| S3 버킷 | 소스 및 데이터 저장 |

### IAM Role 권한

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-northeast-2:*:secret:slack-bots/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::slack-bots-prod-snapshots",
        "arn:aws:s3:::slack-bots-prod-snapshots/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "*"
    }
  ]
}
```

### Secrets Manager 설정

```bash
# Slack Bot Token (xoxb-...)
aws secretsmanager create-secret \
    --name slack-bots/slack-bot-token \
    --secret-string "xoxb-your-bot-token" \
    --region ap-northeast-2

# Slack App Token (xapp-...) - Socket Mode용
aws secretsmanager create-secret \
    --name slack-bots/slack-app-token \
    --secret-string "xapp-your-app-token" \
    --region ap-northeast-2
```

### S3 버킷 생성

```bash
aws s3 mb s3://slack-bots-prod-snapshots --region ap-northeast-2
```

---

## 배포 절차

### Step 1: 로컬에서 S3로 소스 업로드

```bash
# 프로젝트 루트에서 실행
./scripts/deploy-to-s3.sh
```

이 스크립트는:
- 소스 코드를 ZIP으로 압축
- `s3://slack-bots-prod-snapshots/source/slack-bots-source.zip`로 업로드

### Step 2: EC2 초기 설정 (최초 1회)

```bash
# 1. SSH로 EC2 접속
ssh -i your-key.pem ubuntu@<ec2-public-ip>

# 2. 임시 디렉토리에서 소스 다운로드
cd /tmp
aws s3 cp s3://slack-bots-prod-snapshots/source/slack-bots-source.zip . --region ap-northeast-2
unzip slack-bots-source.zip

# 3. 초기 설정 스크립트 실행 (sudo 필요)
sudo bash scripts/ec2-setup.sh

# 4. SSH 재접속 (docker 그룹 적용)
exit
ssh -i your-key.pem ubuntu@<ec2-public-ip>
```

**ec2-setup.sh가 설치하는 항목:**
- Docker CE
- Python 3.11
- Node.js 20.x
- AWS CLI v2
- uv (Python 패키지 매니저)
- Claude Code CLI
- jq

**ec2-setup.sh가 생성하는 스크립트:**

| 스크립트 | 경로 | 설명 |
|----------|------|------|
| `load-env.sh` | `/home/ubuntu/` | Secrets Manager에서 환경변수 로드 |
| `download-source.sh` | `/home/ubuntu/` | S3에서 최신 소스 다운로드 |
| `start-slack-app.sh` | `/home/ubuntu/` | Slack App 컨테이너 시작 |
| `start-scheduler.sh` | `/home/ubuntu/` | Scheduler 컨테이너 시작 |
| `start-gov-funding.sh` | `/home/ubuntu/` | Gov-Funding 컨테이너 시작 |
| `start-all.sh` | `/home/ubuntu/` | 전체 서비스 시작 |
| `stop-all.sh` | `/home/ubuntu/` | 전체 서비스 중지 |

### Step 3: 서비스 시작

```bash
# 전체 서비스 한번에 시작 (소스 다운로드 + 빌드 + 실행)
/home/ubuntu/start-all.sh
```

또는 개별 서비스 시작:

```bash
# 환경변수 로드 (개별 실행 시 필수)
source /home/ubuntu/load-env.sh

# 개별 서비스 시작
/home/ubuntu/start-slack-app.sh
/home/ubuntu/start-scheduler.sh
/home/ubuntu/start-gov-funding.sh
```

---

## 업데이트 배포

소스 코드 변경 후 재배포:

```bash
# 1. 로컬: S3에 새 소스 업로드
./scripts/deploy-to-s3.sh

# 2. EC2: 서비스 재시작
/home/ubuntu/stop-all.sh
/home/ubuntu/start-all.sh
```

---

## 운영 명령어

### 상태 확인

```bash
# 컨테이너 상태 확인
docker ps

# 실시간 로그 확인
docker logs -f slack-app
docker logs -f scheduler
docker logs -f gov-funding

# 스케줄러 파일 로그 확인
tail -f /home/ubuntu/logs/*.log
```

### 서비스 재시작

```bash
# 전체 재시작
/home/ubuntu/stop-all.sh
/home/ubuntu/start-all.sh

# 개별 재시작
docker restart slack-app
docker restart scheduler
docker restart gov-funding
```

### 서비스 중지

```bash
# 전체 중지
/home/ubuntu/stop-all.sh

# 개별 중지
docker stop slack-app
docker stop scheduler
docker stop gov-funding
```

### 컨테이너 정리

```bash
# 중지된 컨테이너 삭제
docker container prune -f

# 사용하지 않는 이미지 삭제
docker image prune -f

# 전체 정리 (주의: 모든 미사용 리소스 삭제)
docker system prune -f
```

---

## 프로젝트 구조

```
slack-bots/
├── src/
│   ├── slack_app/               # Slack App (Socket Mode)
│   │   └── app.py
│   ├── scheduler/               # 업무 자동화 스케줄러
│   │   └── scheduler.py
│   └── gov_funding/             # 정부 지원사업 모니터링
│       ├── main.py              # APScheduler 기반 메인
│       ├── crawlers/            # K-Startup, Bizinfo 크롤러
│       ├── analyzers/           # Bedrock AI 필터링
│       ├── notifiers/           # Slack, Gmail 알림
│       ├── storage/             # S3 스냅샷 저장
│       └── utils/
├── docker/
│   ├── slack-app/
│   │   └── Dockerfile
│   ├── scheduler/
│   │   └── Dockerfile
│   └── gov-funding/
│       └── Dockerfile
├── scripts/
│   ├── deploy-to-s3.sh          # S3 배포 스크립트
│   └── ec2-setup.sh             # EC2 초기 설정
├── docker-compose.yml           # 로컬 개발용
├── requirements.txt
├── requirements-slack-app.txt
└── requirements-gov-funding.txt
```

---

## 환경변수

### 공통

| 변수 | 설명 | 소스 |
|------|------|------|
| `SLACK_BOT_TOKEN` | Bot Token (xoxb-...) | Secrets Manager |
| `SLACK_APP_TOKEN` | App Token (xapp-...) | Secrets Manager |
| `AWS_DEFAULT_REGION` | AWS 리전 | 하드코딩 (ap-northeast-2) |

### Gov-Funding

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `S3_BUCKET` | 스냅샷 저장 버킷 | gov-funding-monitor-snapshots |
| `SLACK_CHANNEL_ID` | 알림 채널 ID | - |
| `GOV_FUNDING_CHANNEL_ID` | Gov-Funding Q&A 채널 ID | - |
| `RELEVANCE_THRESHOLD` | AI 필터링 임계값 | 0.7 |
| `DEADLINE_ALERT_DAYS` | 마감 임박 기준일 | 7 |

---

## 트러블슈팅

### Docker 권한 오류

```bash
# docker 그룹에 사용자 추가 후 재접속 필요
sudo usermod -aG docker $USER
exit
# 재접속
```

### Secrets Manager 접근 오류

```bash
# IAM Role 권한 확인
aws sts get-caller-identity

# Secret 접근 테스트
aws secretsmanager get-secret-value \
    --secret-id slack-bots/slack-bot-token \
    --region ap-northeast-2
```

### S3 다운로드 실패 (403 Forbidden)

```bash
# IAM Role에 S3 권한 확인
aws s3 ls s3://slack-bots-prod-snapshots/ --region ap-northeast-2

# 권한이 없으면 IAM Role에 S3 정책 추가 필요
# terraform/modules/ec2/main.tf에 aws_iam_role_policy 추가
```

### 컨테이너 시작 실패

```bash
# 상세 로그 확인
docker logs slack-app 2>&1 | tail -50

# 컨테이너 내부 확인
docker exec -it slack-app /bin/bash
```

---

## 보안 설계

### 네트워크
- **Private Subnet** 배치 권장 (인터넷 직접 노출 없음)
- **NAT Gateway**를 통한 아웃바운드만 허용
- **Security Group**: 아웃바운드 443만 허용, 인바운드 차단
- **Socket Mode**: 인바운드 포트 불필요

### 인증
- **SSM Session Manager**: SSH 없이 EC2 관리 가능
- **IMDSv2 강제**: 메타데이터 보안 강화
- **Secrets Manager**: 토큰/키 안전 저장

### 모니터링
- **CloudWatch Logs**: 컨테이너 로그 수집
- **VPC Flow Logs**: 네트워크 트래픽 모니터링

---

## Gov-Funding 채널 Q&A

Slack App은 Gov-Funding 채널(`GOV_FUNDING_CHANNEL_ID`)에서 봇을 멘션하면 S3에 저장된 최신 공고 스냅샷을 컨텍스트로 주입하여 공고 기반 답변을 제공합니다.

### 동작 방식

```
사용자 @봇 멘션 → 채널 ID 확인 → S3 스냅샷 fetch (1시간 캐시) → 공고 컨텍스트 + 질문 → Claude CLI → 스레드 응답
```

- **대상 채널**: `GOV_FUNDING_CHANNEL_ID` 환경변수로 지정된 채널에서만 동작
- **데이터 소스**: S3 버킷의 `snapshots/{kstartup,bizinfo,nipa}/` 경로에서 최신 스냅샷 로드
- **캐시**: 1시간 TTL (데이터 없으면 5분 TTL)
- **다른 채널**: 기존 일반 Claude CLI 응답 유지

### 전제조건

1. `GOV_FUNDING_CHANNEL_ID` 및 `S3_BUCKET` 환경변수 설정
2. Gov-Funding 서비스가 최소 1회 실행되어 S3에 스냅샷이 존재해야 함

### 사용 예시

```
@bot 마감 임박한 공고 알려줘
@bot AI 관련 지원사업 있어?
@bot 중소기업 대상 R&D 지원사업 요약해줘
```

---

## 로컬 개발

```bash
# 환경변수 설정
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_APP_TOKEN="xapp-..."

# Docker Compose로 실행
docker compose up -d

# 로그 확인
docker compose logs -f

# 종료
docker compose down
```

---

## 라이선스

Searchdoc Internal Use
