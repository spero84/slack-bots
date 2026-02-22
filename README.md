# Slack Bots

Slack 기반 업무 자동화 플랫폼. 정부 지원사업 공고 모니터링, AI/ML 뉴스 크롤링, 업무 워크플로우 자동화, Claude CLI 연동 Slack 봇을 EC2에서 systemd 서비스로 운영한다.

4개의 독립 서비스가 각각 별도 Python 가상환경(uv)으로 분리되어 있으며, S3를 통해 소스 코드를 배포하고 systemd로 프로세스를 관리한다.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                      EC2 Instance                    │
│                                                      │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │  slack-app    │  │  scheduler   │                 │
│  │  (Socket Mode)│  │  (평일 2h)   │                 │
│  │  Claude CLI   │  │  Notion/Gmail│                 │
│  └──────┬───────┘  └──────┬───────┘                 │
│         │                  │                         │
│  ┌──────┴───────┐  ┌──────┴───────┐                 │
│  │  gov-funding  │  │  ai-news     │                 │
│  │  (매일 9시)   │  │  (월수금 8시) │                 │
│  │  9개 크롤러   │  │  17개 크롤러  │                 │
│  └──────┬───────┘  └──────┬───────┘                 │
│         │                  │                         │
│         └────────┬─────────┘                         │
│                  ▼                                    │
│  ┌──────────────────────────────┐                    │
│  │  AWS (S3 Vectors, Bedrock)   │                    │
│  │  Slack API / Gmail API       │                    │
│  └──────────────────────────────┘                    │
└─────────────────────────────────────────────────────┘
```

## Services

### 1. slack-app — Slack Socket Mode 봇

Claude CLI를 subprocess로 호출하여 Slack 메시지에 응답하는 봇.

- **DM**: 사용자별 세션 유지
- **채널 멘션**: 스레드별 세션 유지
- **gov-funding 채널**: S3 Vectors 검색으로 관련 공고 컨텍스트 주입
- **ai-news 채널**: S3 Vectors 검색으로 관련 기사 컨텍스트 주입
- venv: `/home/ubuntu/venvs/slack-app`

### 2. scheduler — 업무 자동화 워크플로우

Claude CLI로 Notion Kanban 확인, Gmail 요약/초안 작성, Slack 보고를 자동 실행.

- 스케줄: 평일 9, 11, 13, 15, 17시 (APScheduler, Asia/Seoul)
- MCP 도구: Notion, Gmail, Slack, Calendar
- venv: `/home/ubuntu/venvs/scheduler`

### 3. gov-funding — 정부 지원사업 모니터링

9개 정부 사이트에서 지원사업 공고를 크롤링하고, 관련성 필터링 후 알림 전송.

- **크롤러**: K-Startup, 기업마당(Bizinfo), NIPA, NIA, IITP, JOINTIPS, MSIT(과기정통부), MSS(중기부), MOTIE(산자부)
- **Playwright 의존**: KStartup, IITP, MSIT, MOTIE (없으면 자동 스킵)
- **워크플로우**: crawl → deduplicate → deadline_filter(2개월) → keyword_filter → bedrock_filter(Claude) → S3 Vectors 비교 → Slack/Gmail 알림
- 스케줄: 매일 9시 (시작 시 1회 강제 실행)
- venv: `/home/ubuntu/venvs/gov-funding`

### 4. ai-news — AI 뉴스 모니터링

17개 소스에서 AI/ML 관련 뉴스와 논문을 크롤링하고 요약하여 알림 전송.

- **크롤러**: arXiv, Hacker News, TechCrunch, Anthropic, OpenAI, DeepMind, Hugging Face, AI Times, ITWorld, ETNews, IT Daily, AWS Blog, Azure Blog, Google Blog, MS Research, Google Research, Medium
- **카테고리**: Paper, Company News, Industry News
- **워크플로우**: crawl → deduplicate(제목 유사도) → S3 Vectors 신규 식별 → bedrock_summarize(중요도 평가) → vector storage → Slack 알림
- 스케줄: 월/수/금 8시 (시작 시 1회 실행)
- venv: `/home/ubuntu/venvs/ai-news`

## Prerequisites

- **Python 3.13** (deadsnakes PPA)
- **uv** (Python 패키지 매니저)
- **Node.js 22.x** (Claude Code 런타임)
- **Claude Code CLI** (`~/.local/bin/claude`)
- **AWS CLI v2** (S3, Bedrock 접근)
- **Slack App** (Bot Token + App Token, Socket Mode 활성화)
- **Gmail API** credentials (선택)

## Quick Start

```bash
# 1. EC2 초기 설정 (Python, Node.js, uv, Claude Code, AWS CLI, systemd 서비스)
sudo ./scripts/ec2-setup.sh

# 2. 소스 다운로드
/home/ubuntu/download-source.sh

# 3. 환경변수 설정 (.env가 S3에서 자동 다운로드되지 않은 경우)
cp .env.example .env
vi .env

# 4. Python 가상환경 설정 (uv 기반)
/home/ubuntu/setup-venvs.sh

# 5. 전체 서비스 시작
/home/ubuntu/start-all.sh

# 6. 상태 확인
/home/ubuntu/status.sh
```

## Deployment

S3 기반 배포 파이프라인. 소스 코드를 zip으로 압축하여 S3에 업로드하고, EC2에서 다운로드하여 서비스를 재시작한다.

### 배포 흐름

```
[로컬] deploy-to-s3.sh
    ↓  zip 압축 → s3://slack-bots-prod-snapshots/source/slack-bots-source.zip
    ↓  .env도 함께 업로드 (AES256 암호화)
[EC2] download-source.sh
    ↓  서비스 중지 → S3 다운로드 → 압축 해제 → .env 복원
[EC2] setup-venvs.sh
    ↓  uv로 4개 venv 재생성 (/home/ubuntu/venvs/)
    ↓  Playwright chromium 재설치 (gov-funding)
[EC2] restart-all.sh
    ↓  systemctl restart (4개 서비스)
```

### 배포 명령어

```bash
# 로컬에서 실행
./scripts/deploy-to-s3.sh

# EC2에서 실행
/home/ubuntu/download-source.sh
/home/ubuntu/setup-venvs.sh
/home/ubuntu/restart-all.sh
```

### 포함 파일

`deploy-to-s3.sh`는 다음 디렉토리/파일만 zip에 포함:
- `src/` — 소스 코드
- `scripts/` — 운영 스크립트
- `docker/` — Dockerfile
- `requirements*.txt` — 의존성 파일
- `docker-compose.yml`

제외: `*.pyc`, `__pycache__`, `.git`, `.venv`, `.pytest_cache`

## Configuration

### 환경변수 (.env)

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `AWS_REGION` | AWS 리전 | `ap-northeast-2` |
| `S3_BUCKET` | S3 버킷 (gov-funding 벡터) | `gov-funding-monitor` |
| `AI_NEWS_S3_BUCKET` | S3 버킷 (ai-news 벡터) | S3_BUCKET과 동일 |
| `BEDROCK_REGION` | Bedrock 리전 | `us-west-2` |
| `BEDROCK_MODEL_ID` | Bedrock 모델 ID | `global.anthropic.claude-opus-4-6-v1` |
| `EMBEDDING_MODEL_ID` | 임베딩 모델 ID | `amazon.titan-embed-text-v2:0` |
| `SLACK_BOT_TOKEN` | Slack Bot Token | (필수) |
| `SLACK_APP_TOKEN` | Slack App Token (Socket Mode) | (필수) |
| `SLACK_CHANNEL_ID` | gov-funding 알림 채널 | (필수) |
| `GOV_FUNDING_CHANNEL_ID` | gov-funding Q&A 채널 | SLACK_CHANNEL_ID와 동일 |
| `AI_NEWS_CHANNEL_ID` | AI 뉴스 알림/Q&A 채널 | (필수) |
| `GMAIL_CREDENTIALS` | Gmail API credentials JSON | (선택) |
| `EMAIL_RECIPIENTS` | 이메일 수신자 (콤마 구분) | (선택) |
| `RELEVANCE_THRESHOLD` | Bedrock 관련성 임계값 | `0.6` |
| `DEADLINE_ALERT_DAYS` | 마감 알림 기준일 | `7` |
| `AI_NEWS_IMPORTANCE_THRESHOLD` | AI 뉴스 중요도 임계값 | `0.5` |
| `AI_NEWS_MAX_PER_SOURCE` | 소스별 최대 기사 수 | `20` |
| `CLAUDE_CODE_USE_BEDROCK` | Claude Code Bedrock 사용 | `1` |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock Bearer Token | (필수) |
| `ANTHROPIC_API_PROVIDER` | API 프로바이더 | `bedrock` |
| `ANTHROPIC_SMALL_FAST_MODEL` | 빠른 모델 ID | (선택) |

## Directory Structure

```
slack-bots/
├── src/
│   ├── gov_funding/
│   │   ├── main.py               # 워크플로우 (APScheduler, 매일 9시)
│   │   ├── crawlers/
│   │   │   ├── base_crawler.py    # ABC: crawl(), get_detail(), close()
│   │   │   ├── bizinfo_crawler.py
│   │   │   ├── nipa_crawler.py
│   │   │   ├── nia_crawler.py
│   │   │   ├── kstartup_crawler.py   # Playwright
│   │   │   ├── iitp_crawler.py       # Playwright
│   │   │   ├── msit_crawler.py       # Playwright
│   │   │   ├── motie_crawler.py      # Playwright
│   │   │   ├── jointips_crawler.py
│   │   │   └── mss_crawler.py
│   │   ├── analyzers/
│   │   │   ├── bedrock_analyzer.py   # Claude 관련성 분석
│   │   │   └── relevance_filter.py   # 키워드/마감일 필터
│   │   ├── notifiers/
│   │   │   ├── slack_notifier.py
│   │   │   └── gmail_notifier.py
│   │   ├── storage/
│   │   │   ├── models.py            # Source enum, Announcement, NotificationPayload
│   │   │   ├── vector_storage.py    # S3 Vectors (임베딩 저장/검색)
│   │   │   └── s3_storage.py
│   │   └── utils/
│   │       ├── config.py            # Config, CRAWL_SOURCES, RELEVANCE_KEYWORDS
│   │       ├── logger.py
│   │       └── hwp_reader.py        # HWP/HWPX 파일 파싱
│   ├── ai_news/
│   │   ├── main.py               # 워크플로우 (APScheduler, 월수금 8시)
│   │   ├── crawlers/
│   │   │   ├── base_crawler.py    # ABC: crawl(), close()
│   │   │   ├── arxiv_crawler.py
│   │   │   ├── hackernews_crawler.py
│   │   │   ├── techcrunch_crawler.py
│   │   │   ├── anthropic_crawler.py
│   │   │   ├── openai_crawler.py
│   │   │   ├── deepmind_crawler.py
│   │   │   ├── huggingface_crawler.py
│   │   │   ├── aitimes_crawler.py
│   │   │   ├── itworld_crawler.py
│   │   │   ├── etnews_crawler.py
│   │   │   ├── itdaily_crawler.py
│   │   │   ├── aws_blog_crawler.py
│   │   │   ├── azure_blog_crawler.py
│   │   │   ├── google_blog_crawler.py
│   │   │   ├── ms_research_crawler.py
│   │   │   ├── google_research_crawler.py
│   │   │   └── medium_crawler.py
│   │   ├── analyzers/
│   │   │   └── bedrock_summarizer.py  # Claude 요약 및 중요도 평가
│   │   ├── notifiers/
│   │   │   └── slack_notifier.py
│   │   ├── storage/
│   │   │   ├── models.py            # ArticleSource enum, Article, NewsDigest
│   │   │   └── vector_storage.py
│   │   └── utils/
│   │       ├── config.py            # Config, AI_KEYWORDS, CRAWL_SOURCES
│   │       └── logger.py
│   ├── slack_app/
│   │   └── app.py                # Socket Mode 봇 (Claude CLI subprocess)
│   └── scheduler/
│       └── scheduler.py          # 워크플로우 자동화 (Claude CLI subprocess)
├── scripts/
│   ├── ec2-setup.sh              # EC2 초기 설정 (sudo 실행)
│   ├── deploy-to-s3.sh           # 소스 → S3 업로드
│   ├── download-source.sh        # S3 → EC2 다운로드
│   ├── setup-venvs.sh            # uv로 4개 venv 생성
│   ├── start-all.sh              # 전체 서비스 시작
│   ├── stop-all.sh               # 전체 서비스 중지
│   ├── restart-all.sh            # 전체 서비스 재시작
│   ├── status.sh                 # 서비스 상태 확인
│   ├── logs.sh                   # journalctl 로그 확인
│   ├── start-slack-app.sh
│   ├── start-scheduler.sh
│   ├── start-gov-funding.sh
│   ├── start-ai-news.sh
│   ├── test_bedrock.py           # Bedrock 분석 테스트
│   ├── test_slack.py             # Slack 알림 테스트
│   ├── local_test.py             # 로컬 테스트
│   └── build_playwright_layer.sh # Lambda Playwright 레이어 빌드
├── docker/
│   └── ai-news/                  # AI News Docker 설정
├── requirements.txt              # 공통 의존성
├── requirements-gov-funding.txt  # gov-funding 의존성
├── requirements-ai-news.txt      # ai-news 의존성
├── requirements-slack-app.txt    # slack-app 의존성
├── docker-compose.yml
├── .env                          # 환경변수 (커밋 금지)
├── CLAUDE.md                     # Claude Code 프로젝트 메모리
└── workspace/                    # Claude CLI 작업 디렉토리
```

## Operations

```bash
# 상태 확인
/home/ubuntu/status.sh
systemctl status slack-app scheduler gov-funding ai-news

# 로그 확인
/home/ubuntu/logs.sh slack-app        # 실시간 로그
journalctl -u gov-funding --since today  # 오늘 로그
journalctl -u ai-news -n 100            # 최근 100줄

# 재시작
/home/ubuntu/restart-all.sh
sudo systemctl restart gov-funding     # 개별 재시작

# 중지
/home/ubuntu/stop-all.sh
sudo systemctl stop scheduler          # 개별 중지
```

## Development

### 새 크롤러 추가 (gov-funding)

1. `src/gov_funding/storage/models.py`의 `Source` enum에 값 추가
2. `src/gov_funding/utils/config.py`의 `CRAWL_SOURCES`에 사이트 정보 추가
3. `src/gov_funding/crawlers/{name}_crawler.py` 생성 — `BaseCrawler` 상속
4. `crawl()`, `get_detail()`, `close()` 구현
5. `src/gov_funding/crawlers/__init__.py`에 export
6. `src/gov_funding/main.py`에 통합 (Playwright 의존 시 `try/except ImportError`)
7. 테스트 작성 및 실행

### 새 크롤러 추가 (ai-news)

1. `src/ai_news/storage/models.py`의 `ArticleSource` enum에 값 추가
2. `SOURCE_CATEGORY_MAP`에 카테고리 매핑 추가
3. `src/ai_news/utils/config.py`의 `CRAWL_SOURCES`에 소스 URL 추가
4. `src/ai_news/crawlers/{name}_crawler.py` 생성 — `BaseCrawler` 상속
5. `crawl()`, `close()` 구현
6. `src/ai_news/crawlers/__init__.py`에 export
7. `src/ai_news/main.py`에 통합 후 테스트

### 테스트 실행

```bash
# venv 활성화 후 pytest 실행
source /home/ubuntu/venvs/gov-funding/bin/activate && python -m pytest
source /home/ubuntu/venvs/ai-news/bin/activate && python -m pytest

# 개별 테스트
python scripts/test_bedrock.py
python scripts/test_slack.py
```

## Systemd Services

서비스 파일 위치: `/etc/systemd/system/`

### 공통 설정

```ini
[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/slack-bots
EnvironmentFile=/home/ubuntu/slack-bots/.env
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10
```

### 서비스별 실행 명령

| 서비스 | ExecStart |
|--------|-----------|
| slack-app | `/home/ubuntu/venvs/slack-app/bin/python -m src.slack_app.app` |
| scheduler | `/home/ubuntu/venvs/scheduler/bin/python -m src.scheduler.scheduler` |
| gov-funding | `/home/ubuntu/venvs/gov-funding/bin/python -m src.gov_funding.main` |
| ai-news | `/home/ubuntu/venvs/ai-news/bin/python -m src.ai_news.main` |

### daemon-reload

systemd 서비스 파일을 수정한 후:

```bash
sudo systemctl daemon-reload
sudo systemctl restart <service>
```
