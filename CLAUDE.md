# Slack Bots

Slack 기반 업무 자동화 시스템 (정부 지원사업 모니터링, AI 뉴스 크롤링, 워크플로우 스케줄러, Claude CLI 봇)

## Architecture

| 서비스 | 엔트리포인트 | venv | 스케줄 |
|--------|-------------|------|--------|
| slack-app | `src.slack_app.app` | `/home/ubuntu/venvs/slack-app` | 상시 (Socket Mode) |
| scheduler | `src.scheduler.scheduler` | `/home/ubuntu/venvs/scheduler` | 평일 9,11,13,15,17시 |
| gov-funding | `src.gov_funding.main` | `/home/ubuntu/venvs/gov-funding` | 매일 9시 |
| ai-news | `src.ai_news.main` | `/home/ubuntu/venvs/ai-news` | 월/수/금 8시 |

Python 가상환경은 프로젝트 외부 `/home/ubuntu/venvs/` 디렉토리에 서비스별로 분리되어 있다. `scripts/setup-venvs.sh`로 uv를 사용하여 생성한다.

## Common Commands

```bash
# 배포 (로컬 → S3 → EC2)
./scripts/deploy-to-s3.sh                    # 소스 zip 후 S3 업로드
/home/ubuntu/download-source.sh              # EC2에서 S3 다운로드
/home/ubuntu/setup-venvs.sh                  # uv로 venv 재설정
/home/ubuntu/restart-all.sh                  # 전체 서비스 재시작

# 서비스 관리
sudo systemctl start|stop|restart <service>  # slack-app, scheduler, gov-funding, ai-news
/home/ubuntu/status.sh                       # 전체 상태 확인
/home/ubuntu/logs.sh <service>               # journalctl -u <service> -f

# 테스트
source /home/ubuntu/venvs/gov-funding/bin/activate && python -m pytest
source /home/ubuntu/venvs/ai-news/bin/activate && python -m pytest
```

## Code Conventions

- Python 3.13, 한국어 docstring 및 로그 메시지
- Pydantic `BaseModel`로 데이터 모델 정의 (`Announcement`, `Article`, `NotificationPayload`, `NewsDigest`)
- `str` Enum으로 소스 정의 (`Source`, `ArticleSource`)
- async/await 크롤러 패턴 (`BaseCrawler` ABC 상속)
- **uv 기반 가상환경 관리 — pip/venv 직접 사용 금지, 반드시 `uv venv` / `uv pip install` 사용**
- **단위 테스트 필수 — 코드 변경 시 반드시 관련 테스트 작성 및 실행**
- `.env`로 환경변수 관리 (절대 커밋하지 않음)
- **작업 완료 후 반드시 `git push`까지 수행**

## Key Patterns

### BaseCrawler ABC (gov-funding)
```python
class BaseCrawler(ABC):
    source: Source
    name: str
    async def crawl(self, max_items: int = 50) -> list[Announcement]  # 추상
    async def get_detail(self, announcement_id: str) -> Optional[dict]  # 추상
    async def close(self)  # 리소스 정리
```

### BaseCrawler ABC (ai-news)
```python
class BaseCrawler(ABC):
    source: ArticleSource
    name: str
    async def crawl(self, max_items: int = 20) -> list[Article]  # 추상
    async def close(self)  # 리소스 정리
```

### Playwright 크롤러 (optional import)
```python
try:
    from .crawlers import KStartupCrawler
    KSTARTUP_AVAILABLE = True
except ImportError:
    KSTARTUP_AVAILABLE = False
```
KStartup, IITP, MSIT, MOTIE 크롤러가 Playwright 의존.

### 워크플로우 파이프라인
- **gov-funding**: crawl → deduplicate → deadline_filter → keyword_filter → bedrock_filter → vector storage(S3 Vectors) → slack/gmail notify
- **ai-news**: crawl → deduplicate → vector_key 비교(신규 식별) → bedrock_summarize → vector storage → slack notify

## Adding a New Crawler

### gov-funding 새 크롤러 추가
1. `Source` enum에 값 추가 (`src/gov_funding/storage/models.py`)
2. `CRAWL_SOURCES`에 사이트 정보 추가 (`src/gov_funding/utils/config.py`)
3. `src/gov_funding/crawlers/` 에 `{name}_crawler.py` 생성 — `BaseCrawler` 상속, `crawl()`, `get_detail()`, `close()` 구현
4. `src/gov_funding/crawlers/__init__.py`에 export 추가
5. `src/gov_funding/main.py`에 크롤러 인스턴스 및 워크플로우 통합
6. Playwright 필요 시 `try/except ImportError` 패턴 적용
7. 테스트 작성 및 실행

### ai-news 새 크롤러 추가
1. `ArticleSource` enum에 값 추가 (`src/ai_news/storage/models.py`)
2. `SOURCE_CATEGORY_MAP`에 카테고리 매핑 추가
3. `CRAWL_SOURCES`에 소스 URL 추가 (`src/ai_news/utils/config.py`)
4. `src/ai_news/crawlers/` 에 `{name}_crawler.py` 생성 — `BaseCrawler` 상속, `crawl()`, `close()` 구현
5. `src/ai_news/crawlers/__init__.py`에 export 추가
6. `src/ai_news/main.py`에 크롤러 인스턴스 추가
7. 테스트 작성 및 실행

## Directory Structure

```
slack-bots/
├── src/
│   ├── gov_funding/          # 정부 지원사업 모니터링
│   │   ├── crawlers/         # 9개 크롤러 (bizinfo, nipa, nia, kstartup, iitp, jointips, msit, mss, motie)
│   │   ├── analyzers/        # keyword_filter, bedrock_analyzer
│   │   ├── notifiers/        # slack, gmail
│   │   ├── storage/          # models, vector_storage, s3_storage
│   │   └── utils/            # config, logger, hwp_reader
│   ├── ai_news/              # AI 뉴스 모니터링
│   │   ├── crawlers/         # 17개 크롤러 (arxiv, hackernews, techcrunch, anthropic, openai, ...)
│   │   ├── analyzers/        # bedrock_summarizer
│   │   ├── notifiers/        # slack
│   │   ├── storage/          # models, vector_storage
│   │   └── utils/            # config, logger
│   ├── slack_app/            # Slack Socket Mode 봇 (Claude CLI 연동)
│   └── scheduler/            # 업무 자동화 워크플로우 (Notion/Gmail/Slack)
├── scripts/                  # 배포, 서비스 관리, 테스트 스크립트
├── requirements*.txt         # 서비스별 의존성 파일 4개
├── docker-compose.yml
└── .env                      # 환경변수 (커밋 금지)
```

## Environment Variables

`.env` 파일에 다음 키가 필요:

```
AWS_REGION, S3_BUCKET, BEDROCK_REGION, BEDROCK_MODEL_ID, EMBEDDING_MODEL_ID
SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL_ID
GOV_FUNDING_CHANNEL_ID, AI_NEWS_CHANNEL_ID, AI_NEWS_S3_BUCKET
GMAIL_CREDENTIALS, EMAIL_RECIPIENTS
RELEVANCE_THRESHOLD, DEADLINE_ALERT_DAYS
CLAUDE_CODE_USE_BEDROCK, AWS_BEARER_TOKEN_BEDROCK
ANTHROPIC_API_PROVIDER, ANTHROPIC_SMALL_FAST_MODEL
```

## Testing

```bash
# venv 활성화 후 테스트 실행
source /home/ubuntu/venvs/gov-funding/bin/activate
python -m pytest

source /home/ubuntu/venvs/ai-news/bin/activate
python -m pytest

# 개별 테스트 스크립트
python scripts/test_bedrock.py   # Bedrock 분석 테스트
python scripts/test_slack.py     # Slack 알림 테스트
```

**규칙: 코드를 변경하면 반드시 관련 단위 테스트를 작성하고 실행한다.**

## Deployment

uv 기반 배포 파이프라인:

1. **deploy-to-s3** — 로컬에서 소스를 zip 압축 후 `s3://slack-bots-prod-snapshots/source/` 업로드
2. **download-source** — EC2에서 S3 zip 다운로드 후 `/home/ubuntu/slack-bots/`에 압축 해제
3. **setup-venvs** — uv로 4개 가상환경 생성/재설정 (`/home/ubuntu/venvs/`)
4. **systemctl restart** — 4개 systemd 서비스 재시작

```bash
# 전체 배포 흐름
./scripts/deploy-to-s3.sh           # 1. 로컬 → S3
ssh ec2 "/home/ubuntu/download-source.sh"   # 2. S3 → EC2
ssh ec2 "/home/ubuntu/setup-venvs.sh"       # 3. venv 설정
ssh ec2 "/home/ubuntu/restart-all.sh"       # 4. 서비스 재시작
```

**작업 완료 후 반드시 `git add`, `git commit`, `git push`까지 수행할 것.**
