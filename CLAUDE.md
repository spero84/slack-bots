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
- **`uv pip install`로 패키지 설치 시 해당 서비스의 `requirements*.txt`에 반드시 추가**
- **단위 테스트 필수 — 코드 변경 시 반드시 관련 테스트 작성 및 실행**
- `.env`로 환경변수 관리 (절대 커밋하지 않음)
- **작업 완료 후 반드시 `git pull` → `git push` 수행 (push 전에 항상 pull 먼저)**
- **`src/` 코드 수정 후 반드시 해당 서비스 재시작 (`sudo systemctl restart <service>`) — 서비스 매핑: `src/slack_app/` → slack-app, `src/scheduler/` → scheduler, `src/gov_funding/` → gov-funding, `src/ai_news/` → ai-news**

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

### Data Models

#### Announcement (gov-funding)
| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | str | 공고 고유 ID (pbancSn/pblancId) |
| `source` | Source | 출처 |
| `title` | str | 공고 제목 |
| `category` | Optional[str] | 분야/카테고리 |
| `deadline` | Optional[datetime] | 마감일 |
| `d_day` | Optional[int] | D-day 값 |
| `department` | Optional[str] | 소관부처 |
| `organization` | Optional[str] | 주관기관 |
| `url` | str | 상세 페이지 URL |
| `posted_date` | Optional[datetime] | 공고 게시일 |
| `summary` | Optional[str] | AI 요약 |
| `relevance_score` | Optional[float] | 관련성 점수 (0-1) |
| `crawled_at` | datetime | 크롤링 시각 |

Computed fields: `is_deadline_soon` (0 < d_day <= 7), `normalized_title` (공백·특수문자 제거, 소문자)

#### Article (ai-news)
| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | str | 소스별 고유 ID |
| `source` | ArticleSource | 출처 |
| `category` | ArticleCategory | 카테고리 (paper/company/industry) |
| `title` | str | 제목 |
| `url` | str | 원본 URL |
| `authors` | Optional[list[str]] | 저자 목록 |
| `published_at` | Optional[datetime] | 게시일 |
| `summary` | Optional[str] | 원본 요약/초록 |
| `ai_summary` | Optional[str] | Bedrock 생성 한국어 요약 |
| `importance_score` | Optional[float] | 중요도 점수 (0-1) |
| `tags` | list[str] | 태그 목록 |
| `extra` | Optional[dict] | 소스별 추가 데이터 (HN score 등) |
| `crawled_at` | datetime | 크롤링 시각 |

Computed fields: `normalized_title` (중복 체크용), `vector_key` (`{source}_{id}`)

#### NotificationPayload (gov-funding)
- `new_announcements`, `deadline_soon`, `updated_announcements` — computed: `has_content`, `total_count`

#### NewsDigest (ai-news)
- `papers`, `company_news`, `industry_news` — computed: `has_content`, `total_count`
- `from_articles()` 클래스 메서드: 기사 목록에서 카테고리별 그룹화 (중요도 정렬)

### Deduplication

**gov-funding** — 소스 우선순위 기반 (`normalized_title` 비교, 첫 번째 일치 항목만 유지):
```
K-Startup > Bizinfo > NIPA > NIA > IITP > JOINTIPS > MSIT > MSS > MOTIE
```

**ai-news** — 2-pass 중복 제거:
1. **1차**: `normalized_title` 정확 일치 제거
2. **2차**: 한국 뉴스 소스 간 `SequenceMatcher` 유사도 0.6 이상 제거 (대상: aitimes, itworld, etnews, itdaily)

### Error Recovery

| 상황 | 폴백 전략 |
|------|----------|
| Bedrock 분석 실패 (gov-funding) | 키워드 필터 결과만 사용 |
| Bedrock 개별 분석 에러 (gov-funding) | `relevance_score=0.5` 할당 |
| Vector 키 조회 실패 (ai-news) | 전체 기사를 신규로 처리 |
| Bedrock 요약 실패 (ai-news) | 요약 없이 전송 |
| 벡터 저장 실패 (gov-funding) | 모든 공고를 신규로 취급하여 알림 |
| 크롤러 개별 실패 | 해당 소스 스킵, 나머지 계속 실행 |

### Vector Storage (S3 Vectors)

| 항목 | gov-funding | ai-news |
|------|-------------|---------|
| 인덱스명 | `announcements` | `ainewsarticles` |
| 차원 | 1024 | 1024 |
| 거리 메트릭 | cosine | cosine |
| 데이터 타입 | float32 | float32 |
| 배치 크기 | 10 | 10 |
| 임베딩 모델 | `amazon.titan-embed-text-v2:0` | `amazon.titan-embed-text-v2:0` |
| 임베딩 텍스트 | 제목 + 요약 | 제목 + ai_summary (또는 summary) |

### Bedrock Integration

| 항목 | gov-funding (분석) | ai-news (요약) |
|------|-------------------|----------------|
| API 버전 | `bedrock-2023-05-31` | `bedrock-2023-05-31` |
| 모델 | `global.anthropic.claude-opus-4-6-v1` | `global.anthropic.claude-opus-4-6-v1` |
| 리전 | `us-west-2` | `us-west-2` |
| temperature | 0.1 | 0.1 |
| max_tokens | 500 | 1000 |
| 임계값 기본값 | 0.6 (관련성) | 0.5 (중요도) |

### Slack App Internals

- **세션 관리**: DM = 사용자별 세션, 채널 멘션 = 스레드별 세션
- **Claude CLI 플래그**: `--session-id` (새 세션) / `--resume` (기존), `--max-turns 25`, `--output-format text`, timeout 600초
- **Vector 검색**: top_k=5, 결과 부족 시 메타데이터 필터 제거 후 재검색
  - gov-funding 채널: `announcements` 인덱스, 소스/D-day 필터
  - ai-news 채널: `ainewsarticles` 인덱스, 소스/카테고리 필터
- **SYSTEM_RULES**: Slack mrkdwn 형식만 사용, 테이블 금지, `*굵게*` 대신 `#` 헤더 금지

### Scheduler Workflow

4단계 워크플로우 (평일 9, 11, 13, 15, 17시, 채널 `C0AEW7LF0RJ`):

1. **Notion Kanban 확인** — Shawn이 Assignee/Reviewer인 태스크 (Ready/In Progress/In Review)
2. **Gmail 확인 및 라벨링** — 최근 1시간 메일, 발신자 기준 자동 라벨링 (회의록, CSP, 청구서, 정부지원사업, VC 등)
3. **메일 초안 작성** — To에 본인 이메일이 직접 포함된 외부 발신 메일만 (절대 전송 금지, 초안만)
4. **Slack 결과 보고** — Notion 현황 + Gmail 요약 + 초안 목록 + 오늘의 액션 가이드

MCP 도구: Notion, Gmail, Slack, Calendar

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
│   │   └── utils/            # config, logger, file_reader
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

## Configuration Constants

### gov-funding
- **RELEVANCE_KEYWORDS** (54개): IT/SW (IT, ICT, SW, 디지털, 클라우드, SaaS), AI (AI, 인공지능, 머신러닝, LLM, 생성형), Startup (창업, 스타트업, 벤처, 스케일업), Support (R&D, 연구개발, 바우처, 보조금)
- **EXCLUDE_KEYWORDS** (26개): 농업, 축산, 수산, 제조업, 건설, 조선, 에너지, 바이오, 관광 등 비IT 분야
- **ALLOWED_REGIONS** (3개): 서울, 경기, 성남
- **EXCLUDE_REGIONS** (17개): 부산, 대구, 인천, 광주, 대전, 울산, 세종, 강원 등
- **마감 마일스톤**: `[7, 3]`일 — D-day가 마일스톤을 통과할 때 알림 (예: 이전 D-8 → 현재 D-7)

### ai-news
- **AI_KEYWORDS** (64개): 모델/기술 (LLM, GPT, Claude, transformer), 분야 (AI, NLP, computer vision), 회사 (OpenAI, Anthropic, DeepMind), 도구 (FAISS, LangChain, LlamaIndex), 한국어 (인공지능, 딥러닝)
- **ARXIV_CATEGORIES** (4개): `cs.AI`, `cs.CL`, `cs.CV`, `cs.LG`
- **SOURCE_CATEGORY_MAP**: Paper (arxiv, huggingface, ms_research, google_research), Company (anthropic, openai, deepmind, aws_blog, azure_blog, google_blog), Industry (techcrunch, hackernews, aitimes, itworld, etnews, itdaily, medium)

## Dependencies

| 서비스 | 주요 패키지 |
|--------|------------|
| 공통 | pydantic, requests, beautifulsoup4, lxml, boto3, python-dateutil |
| gov-funding | playwright, apscheduler, olefile (HWP 파싱), google-api-python-client |
| ai-news | feedparser, slack_sdk, apscheduler |
| slack-app | slack-bolt, slack-sdk, python-dotenv, boto3 |
| 테스트 | pytest, pytest-asyncio, moto |

## Systemd Services

서비스 파일 위치: `/etc/systemd/system/`

공통 설정:
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

| 서비스 | ExecStart |
|--------|-----------|
| slack-app | `/home/ubuntu/venvs/slack-app/bin/python -m src.slack_app.app` |
| scheduler | `/home/ubuntu/venvs/scheduler/bin/python -m src.scheduler.scheduler` |
| gov-funding | `/home/ubuntu/venvs/gov-funding/bin/python -m src.gov_funding.main` |
| ai-news | `/home/ubuntu/venvs/ai-news/bin/python -m src.ai_news.main` |

서비스 파일 수정 후: `sudo systemctl daemon-reload && sudo systemctl restart <service>`

## Operations

```bash
# 전체 상태 확인
/home/ubuntu/status.sh
systemctl status slack-app scheduler gov-funding ai-news

# 실시간 로그
/home/ubuntu/logs.sh <service>           # journalctl -u <service> -f
journalctl -u gov-funding --since today  # 오늘 로그
journalctl -u ai-news -n 100            # 최근 100줄

# 재시작
/home/ubuntu/restart-all.sh              # 전체 재시작
sudo systemctl restart gov-funding       # 개별 재시작

# 중지
/home/ubuntu/stop-all.sh                 # 전체 중지
sudo systemctl stop scheduler            # 개별 중지
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

**작업 완료 후 반드시 `git add`, `git commit`, `git pull`, `git push` 수행할 것. (push 전에 항상 pull 먼저)**
