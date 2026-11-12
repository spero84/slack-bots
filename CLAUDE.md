# Slack Bots

Slack 기반 업무 자동화 시스템 (정부 지원사업 모니터링, AI 뉴스 크롤링, 워크플로우 스케줄러, Claude CLI 봇)

## Architecture

| 서비스 | 엔트리포인트 | venv | 스케줄 |
|--------|-------------|------|--------|
| slack-app | `src.slack_app.app` | `/home/ubuntu/venvs/slack-app` | 상시 (Socket Mode) |
| scheduler | `src.scheduler.scheduler` | `/home/ubuntu/venvs/scheduler` | 평일 9,13,17시 |
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

# 테스트 (서비스별 venv에서 해당 디렉토리만)
/home/ubuntu/venvs/gov-funding/bin/python -m pytest tests/gov_funding
/home/ubuntu/venvs/ai-news/bin/python -m pytest tests/ai_news
/home/ubuntu/venvs/slack-app/bin/python -m pytest tests/slack_app
/home/ubuntu/venvs/scheduler/bin/python -m pytest tests/test_scheduler.py tests/test_mail_cleanup.py tests/test_kanban.py tests/test_mail_digest.py

# scheduler 일일 보고 1️⃣ Kanban 섹션 미리보기 (Notion 조회만, 전송 없음)
set -a; . ./.env; set +a
/home/ubuntu/venvs/scheduler/bin/python -m src.scheduler.kanban

# scheduler 메일 자동 정리 미리보기 (지우지 않고 대상만 JSON 출력)
/home/ubuntu/venvs/scheduler/bin/python -m src.scheduler.mail_cleanup --hours 72 --dry-run

# scheduler 2️⃣ 중요 메일 요약 섹션 미리보기 (Gmail 읽기 + Bedrock 요약만, 메일 변경·전송 없음)
# --show-prompt: 모델 입력(스레드별 본문)도 출력. 정리(휴지통 이동)를 거치지 않아 광고·알림이 섞인다
/home/ubuntu/venvs/scheduler/bin/python -m src.scheduler.mail_digest --hours 16

# gov-funding 수동 실행 (스케줄러 없이 1회) — systemd와 달리 .env를 직접 불러와야 한다
set -a; . ./.env; set +a
/home/ubuntu/venvs/gov-funding/bin/python -m src.gov_funding.main --dry-run  # 알림/저장 없이 결과 JSON 출력
/home/ubuntu/venvs/gov-funding/bin/python -m src.gov_funding.main --seed     # 알림 없이 저장만
/home/ubuntu/venvs/gov-funding/bin/python -m src.gov_funding.main --once     # 실제 1회 실행
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
MSIT, MOTIE의 **목록 페이지만** Playwright를 쓴다 (JS 렌더링). 상세 페이지는 정적 HTML이라 requests로 받고, Playwright는 폴백일 뿐이다. 나머지 크롤러는 전부 requests 기반이다. `crawlers/__init__.py`가 MSIT/MOTIE를 `try/except ImportError`로 import하고, `main.create_crawlers()`는 import되지 않은 크롤러를 경고 후 건너뛴다.

- **chromium에 `--single-process`를 절대 넣지 말 것.** 이 모드는 브라우저 수명 동안 페이지(컨텍스트)를 하나만 만들 수 있어 두 번째 `new_page()`에서 chromium이 죽는다 (`Target page, context or browser has been closed`). 2026-09~10월 MSIT 상세 수집이 전부 실패한 원인이었다. `tests/gov_funding/test_playwright_browser.py`가 이 플래그를 검사한다.
- `_init_browser()`의 `browser.is_connected()` 확인은 안전장치로 유지한다 (죽은 브라우저 재사용 방지).

### 소스별 수집 주의사항 (gov-funding)

크롤러가 0건을 반환해도 파이프라인은 에러 없이 끝나므로 **0건이면 반드시 WARNING**을 남긴다. `main.py`는 0건 소스와 크롤러 예외를 Slack 알림에 `⚠️ 수집 이상`으로 함께 표시한다.

| 소스 | 수집 방식 | 주의 |
|------|----------|------|
| 기업마당 | `selectSIIA200View.do?schEndAt=N&cpage=N` HTML | 페이지당 15건 고정(`rows` 무시). 등록일 기준 최근 `lookback_days`(5)일까지 페이지 이동. ID는 href의 `pblancId` (`hashCode=`는 항상 빈 값). 신청기간은 **마지막 날짜**가 마감일 ('2026-10-16 ~ 2026-10-26' → 10-26) |
| K-Startup | `bizpbanc-ongoing.do?page=N&pbancClssCd=PBC010/PBC020` HTML | 무한 스크롤이 아니라 서버 페이지네이션. `suptReginCdList=LC00,LC01,LC08`(전국/서울/경기). 마감된 공고 상세는 `bizpbanc-deadline.do` |
| IITP | IRIS `retrieveBsnsAncmBtinSituList.do` (sorgnId=10004) JSON | www.iitp.kr은 robots.txt가 `Disallow: /`이고 목록이 Vue 렌더링이라 IRIS를 쓴다. 접수중(`ancmIng`)+접수예정(`ancmPre`) |
| JOINTIPS | `jointips.or.kr/api/cms/public/event/list` JSON | 2026-09 CMS 이전으로 옛 `bbs/board.php` 경로는 404 |
| MSIT | 목록 Playwright, 상세 `view.do?...&bbsSeqNo=100&nttSeqNo=` | `bbsSeqNo=100`이 빠지면 HTTP 200 '시스템 점검 안내' 페이지가 온다. 첨부는 `fn_download()` onclick → `fileDown.do` |
| MOTIE | 목록 Playwright, 상세 `motir.go.kr/kor/article/ATCL2826a2625/{id}/view` | ID는 게시물 번호만 (공고번호/해시로 대체 금지 — 404 링크·키 변경). 첨부 이름의 ` [178.7 KB]` 제거 |
| MSS | HTML | 본문은 숨은 `textarea#editContents`(이스케이프된 HTML) |
| NIA/NIPA | HTML | NIA `span.writer em` 마지막 = 팀명, NIPA 상세는 `table.tb05`의 '내용' 행 |

MSIT/MOTIE/MSS 등 목록에 마감일이 없는 공고는 Bedrock 단계에서 상세/첨부 텍스트로 `utils/deadline_text.parse_deadline_from_text()`가 마감일을 채운다.

### RSS 소스 주의사항 (ai-news)

HTML 스크래핑보다 RSS가 안정적이다. 실제로 세 소스가 조용히 고장난 적이 있다 — 크롤러가 0건을 반환해도 파이프라인은 에러 없이 끝나므로, **피드가 비어 있으면 반드시 WARNING을 남긴다**.

| 소스 | 정상 URL | 고장 원인 |
|------|----------|-----------|
| arXiv | `https://rss.arxiv.org/rss/{cat}` | `export.arxiv.org/rss/{cat}`는 HTTP 200 + 유효 XML인데 item이 0건 |
| OpenAI | `https://openai.com/news/rss.xml` | `openai.com/{index,blog,research}/` HTML 전부 403 (봇 차단) |
| ETNews | `https://rss.etnews.com/04046.xml` (AI), `04043.xml` (SW) | `/news/article_list/AI` 경로 404. 섹션 목록은 `https://www.etnews.com/rss/` |

- arXiv RSS는 `arxiv_announce_type`이 `new`/`cross`/`replace`/`replace-cross`다. `replace*`는 기존 논문 개정판이라 제외한다.
- arXiv는 같은 발표 회차 논문에 **동일한 `pubDate`**를 부여하므로 시간 정렬이 무의미하다. 카테고리를 라운드로빈으로 병합해 편중을 막는다.
- arXiv 제목에는 카테고리 접두어가 없다. `^[\w.]+:\s*`로 접두어를 제거하면 `TANGO: Humanoid...` 같은 제목의 앞부분이 잘린다.
- `requests.exceptions.HTTPError`의 상태 코드를 볼 때 `if e.response:`는 쓰지 말 것 — `Response.__bool__`이 4xx/5xx에서 False라 403이 분기를 타지 못한다. `e.response is not None`으로 비교한다.

### 워크플로우 파이프라인
- **gov-funding**: crawl(소스별 격리) → deduplicate → deadline_filter → keyword_filter → S3 Vectors 조회로 분류(저장된 공고/다른 소스로 저장된 같은 사업/신규) → **신규만** bedrock_filter(상세+첨부, 마감일 추출) → slack/gmail notify → **알림 성공 후** vector upsert
  - 저장된 공고는 저장된 점수/요약을 재사용하고 Bedrock을 다시 부르지 않는다
  - 같은 제목(`canon_title`)이 다른 소스/ID로 이미 저장돼 있으면 재알림하지 않는다 (저장 항목이 마감 전이고 최근 60일 안에 수집된 경우만)
  - Bedrock 탈락 공고는 로컬 캐시(`$GOV_FUNDING_STATE_DIR/rejected.json`, 기본 `/home/ubuntu/.local/state/gov-funding`)에 기록해 마감일까지(마감일 모르면 14~20일) 재분석하지 않는다. 알림은 됐지만 벡터 저장에 실패한 공고도 여기에 기록해 다음 날 재알림을 막는다
  - 목록에 마감일이 없는 소스(MSIT/MOTIE/NIA 등)는 저장된 마감일(처음 분석 때 첨부에서 추출)을 마감일 필터 전에 복원한다 — 복원하지 않으면 재저장 때 마감일이 지워져 리마인더가 나가지 않는다
  - 마감 리마인더(D-7, D-3)는 오늘 수집된 저장 공고 + 수집 범위 밖으로 밀려난 저장 공고(저장된 마감일 기준) 대상
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

Computed fields: `is_deadline_soon` (0 < d_day <= 7), `normalized_title` (= `canon_title(title)`, 중복 체크용)

날짜는 KST 기준이다 (`utils/dates.py`: `now_kst`, `today_kst`, `calculate_d_day`). 서버(EC2) 시간대가 UTC라 naive `datetime.now()`로 D-day를 계산하면 15:00 UTC 이후 실행에서 하루 어긋난다.

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

**gov-funding** — 소스 우선순위 기반 (`main.CRAWLER_SPECS` 순서):
```
K-Startup > Bizinfo > NIPA > NIA > IITP > JOINTIPS > MSIT > MSS > MOTIE
```
1. **1차**: `canon_title()`(`storage/models.py`, `Announcement.normalized_title`과 동일) 정확 일치. 가운뎃점 변형(`·ㆍ∙‧`), 'YYYY년도'→'YYYY년', 괄호/따옴표, 앞뒤의 (수정)/(재공고)/(연장)/공고/안내 등을 정규화한다 — 기업마당은 'ㆍ'(U+318D, `\w`에 포함)를 써서 예전 정규화로는 다른 소스와 절대 일치하지 않았다
2. **2차 (다른 소스끼리만)**: `SequenceMatcher` ≥ 0.90 **그리고** 숫자 토큰 동일(한쪽에만 있는 연도 무시) **그리고** 서로의 단어를 모두 포함. ai-news의 0.6은 여기선 '2차 vs 3차', '공급기업 vs 수요기업', '서류평가 vs 발표평가 결과' 같은 별개 공고를 합치므로 쓰지 않는다
3. 남기는 쪽이 이미 마감됐고 중복 쪽이 아직 열려 있으면 열린 쪽을 남긴다 (마감 원공고 + 재공고가 둘 다 사라지는 것 방지)

**ai-news** — 2-pass 중복 제거:
1. **1차**: `normalized_title` 정확 일치 제거
2. **2차**: 한국 뉴스 소스 간 `SequenceMatcher` 유사도 0.6 이상 제거 (대상: aitimes, itworld, etnews, itdaily)

### Error Recovery

| 상황 | 폴백 전략 |
|------|----------|
| Bedrock 사용 불가 (gov-funding: 처음 3건 전부 실패, 또는 실패 3건 이상 & 50% 이상) | `BedrockUnavailableError` → 신규 공고 판정 생략(무검증 공고를 보내지 않음), 리마인더는 발송, Slack에 오류 표시 |
| Bedrock 개별 분석 에러 (gov-funding) | 점수 `None` → 이번 실행에서 제외, 저장/캐시하지 않아 다음 실행에서 재시도 |
| Vector 키 조회 실패 (ai-news) | 전체 기사를 신규로 처리 |
| Bedrock 요약 실패 (ai-news) | 요약 없이 전송 |
| 벡터 인덱스 조회 실패 (gov-funding) | 신규 판별 불가 → 공고 알림 생략, Slack에 오류만 알림 (전부 신규로 보내면 수십 건 재알림) |
| Slack 알림 실패 (gov-funding) | 벡터 저장 생략 → 다음 실행에서 신규/리마인더 재발송 |
| 벡터 저장 일부 실패 (gov-funding) | 나머지 배치는 계속 저장, `VectorUpsertError`로 결과에 기록 |
| 크롤러 개별 실패 | 해당 소스 스킵(`_safe_crawl`), 나머지 계속 실행, Slack에 `⚠️ 수집 이상` 표시 |

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
| 모델 | `global.anthropic.claude-sonnet-5` | `global.anthropic.claude-sonnet-5` |
| 리전 | `us-west-2` | `us-west-2` |
| thinking | `{"type": "disabled"}` | `{"type": "disabled"}` |
| max_tokens | 1024 | 1000 |
| 응답 형식 | structured outputs (`output_config.format` json_schema) | 자유 JSON |
| 임계값 기본값 | 0.6 (관련성, 운영 `.env` `RELEVANCE_THRESHOLD=0.7`) | 0.5 (중요도) |

**Sonnet 5 / Opus 5 주의사항:**
- `temperature`를 보내면 `ValidationException: temperature is deprecated for this model` 발생 — 절대 포함하지 말 것
- Opus 5는 thinking이 기본 ON이라 짧은 `max_tokens`를 thinking이 잠식하고 응답 첫 블록이 `thinking`이 됨. 대량 분류/요약 파이프라인에는 `thinking: {"type": "disabled"}`를 명시한 Sonnet 5를 사용한다
- 응답 파싱은 `content[0]`이 아니라 **첫 `type == "text"` 블록**을 찾아야 한다

**Bedrock 인증:** 모든 `bedrock-runtime` 클라이언트는 `Config(signature_version="v4")`를 명시한다. `.env`의 `AWS_BEARER_TOKEN_BEDROCK`(Claude CLI용)은 만료될 수 있고, 설정되어 있으면 boto3가 EC2 IAM Role 대신 그 토큰으로 인증을 시도해 InvokeModel이 전부 `AccessDeniedException`으로 실패한다.

**Claude CLI 인증 (scheduler, slack-app):** `.env`에 `AWS_BEARER_TOKEN_BEDROCK`을 두지 않는다. 없으면 Claude CLI가 EC2 IAM Role(`s7c-shawn-personal-an2-role-slack-bot`)로 인증하고, 이 Role은 `global.anthropic.claude-opus-4-6-v1`와 `ANTHROPIC_SMALL_FAST_MODEL` 호출 권한이 있다. 만료된 토큰이 남아 있으면 CLI가 IAM Role로 넘어가지 않고 `403 authentication_failed`("Please make sure your API Key is valid")를 10회 재시도한 뒤 종료한다. 2026-08-19~10-28 동안 scheduler 일일 보고가 이 문제로 한 번도 나가지 않았다. 증상은 `~/logs/workflow_*.log`의 `api_retry ... authentication_failed`, `Return Code: 1`이다.

**JSON 응답 파싱:** gov-funding은 structured outputs(`output_config.format` json_schema, min/max 미지원이라 점수는 코드에서 [0,1] clamp)로 유효한 JSON을 받는다. 그래도 폴백은 유지한다: `extract_json_object()` → `extract_fields_fallback()`(정규식으로 점수/요약 복구 — `"reason"` 텍스트가 요약에 섞이지 않게 끊는다) → `(None, None)`(분석 실패). ai-news는 `(0.5, None)`. 파싱 실패 시 원문 300자를 로그에 남긴다. 단순 `re.search(r"\{[^{}]+\}")`는 중첩 객체에서도 깨지므로 쓰지 않는다. 상세/첨부 텍스트와 요약은 `utils/text.clean_text()`로 HTML 태그/엔티티를 제거한다 (MSS 본문의 `&lt;br /&gt;`가 요약에 그대로 옮겨진 적 있음).

### Slack App Internals

- **세션 관리**: DM = 사용자별 세션, 채널 멘션 = 스레드별 세션
- **Claude CLI 플래그**: `--session-id` (새 세션) / `--resume` (기존), `--max-turns 25`, `--output-format text`, timeout 600초
- **Vector 검색**: top_k=5, 결과 부족 시 메타데이터 필터 제거 후 재검색
  - gov-funding D-day는 저장된 `d_day`(마지막 저장 시점 값)가 아니라 `deadline`으로 오늘(KST) 기준 계산한다. '임박/곧 마감/이번 주' 질문은 top_k=30으로 검색 후 마감 7일 이내 + 유사도 상위 2건을 남긴다
  - gov-funding 채널: `announcements` 인덱스, 소스 필터 (마감 임박은 검색 후 마감일로 계산)
  - ai-news 채널: `ainewsarticles` 인덱스, 소스/카테고리 필터
- **SYSTEM_RULES**: Slack mrkdwn 형식만 사용, 테이블 금지, `*굵게*` 대신 `#` 헤더 금지

### Scheduler Workflow

4단계 워크플로우 (평일 9, 13, 17시, 채널 `C0AEW7LF0RJ`):

1. **Notion Kanban** — Claude 실행 전에 `kanban.build_kanban_section()`이 Notion API(`NOTION_TOKEN`, `Notion-Version: 2025-09-03` data_sources)로 3개 보드를 조회하고, Shawn이 `Assign`/`Reviewer`인 Ready (Commit)/In Progress/In Review 태스크만 코드에서 다시 골라 1️⃣ 섹션 문구를 완성한다. Claude는 이 섹션을 그대로 넣고 Notion을 다시 조회하지 않는다. 각 줄은 `` `Part` *제목* — 보드 · 역할(담당/리뷰) · 마감(초과 일수) `` 형식이다
   - 보드별 속성: LT Operation은 `Assign`만 있고 Part 대신 `Work Type`을 쓴다(마감 `Start & Due Date`). Management Support는 `Assign`/`Reviewer`/`Part`/`Deadline`. Searchdoc Operation은 `Assign`/`Reviewer`/`Part`이고 마감은 `Due Date`, 비어 있으면 `Deadline`. 기간이면 end를 마감으로 본다
   - 예전에는 Claude가 Notion MCP로 직접 조회했다. 그런데 없는 `Person` 속성으로 조회해 400이 나면 조건을 임의로 바꿔 재조회했고(2026-10-28~11-04, 리뷰어로만 지정된 태스크 누락), 57KB 조회 결과를 파일로 받아 읽는 등 실행마다 결과가 달랐다. 그래서 조회를 코드로 옮겼다
   - 보드 하나가 실패하면 섹션에 `⚠️ *보드* — 보드 조회 실패`로 표시하고 나머지 보드는 그대로 보고한다. 토큰이 없으면 섹션 전체가 `Notion 조회 실패`로 나간다
   - `Holding`(보류)은 Notion에서 'In progress' 그룹이지만 보고하지 않는다. Kanban 섹션에도 4️⃣ 액션 가이드에도 넣지 않는다 (사용자 요구, 2026-11-12 — 프롬프트에도 명시). `Backlogs`/`Done`/`Archive`도 제외 (`kanban.EXCLUDED_STATUSES`)
2. **Gmail 확인 및 라벨링** — 최근 1시간 메일, 발신자 기준 자동 라벨링 (회의록, CSP, 청구서, 정부지원사업, VC 등)
   - **메일 자동 정리**: Claude 실행 전에 `mail_cleanup.cleanup_mailbox()`가 지난 실행 이후(최대 72시간) 메일 중 광고(제목 맨 앞 `(광고)`/`[광고]`), 미팅 거절(`거절함:`/`거절됨:`/`Declined:`), GitHub 알림(`notifications@github.com`), 보안 알림(Google `보안 알림`/`Security alert`, AWS Control Tower `aws-controltower-AggregateSecurityNotifications`)을 휴지통으로 옮긴다. Gmail 검색어로 찾은 뒤 발신자·제목·수신 시각을 코드에서 다시 확인한다. 별표/보낸/임시보관 메일은 지우지 않는다. Google `중요 보안 알림`과 `noreply@github.com`(인증 코드 등)도 남긴다
   - 삭제를 Claude에 맡기지 않는 이유: Claude가 프롬프트의 `after:` 값을 2024년 기준으로 재계산해 검색한 적이 있다 (2026-11-03~04 4회 중 3회). 같은 방식으로 지우면 범위 밖의 오래된 메일까지 지워진다
   - Gmail 프로모션 탭 메일은 정부기관 안내·서비스 공지도 섞여 있어 일괄 삭제하지 않는다. 후보 목록(ID 포함)을 프롬프트에 넣고, Claude는 그 목록 안의 광고만 `TRASH` 라벨로 옮길 수 있다
   - 인증은 gmail-mcp-server의 OAuth 토큰(`GMAIL_MCP_CREDENTIALS_PATH`, `GMAIL_MCP_TOKEN_PATH`)을 재사용한다. 정리가 실패해도 보고는 나가고, 2️⃣ Gmail 섹션에 오류가 표시된다
   - **📌 중요 메일 요약** (2️⃣ 맨 위): 정리 다음에 `mail_digest.build_digest()`가 같은 시간 범위의 받은 메일(보낸·임시·휴지통·스팸·프로모션 탭 제외)을 본문까지 읽어 스레드로 묶고, Bedrock Sonnet 5(thinking off, structured outputs)가 중요한 스레드만 골라 `분류 · 긴급/중요 · 요약 2~3문장 · 👉 할 일`을 만든다. 섹션 문구는 코드가 완성하고 Claude CLI는 그대로 붙여 넣으며, 4️⃣ 액션 가이드의 메일 항목도 이 섹션의 👉만 근거로 쓴다. 최대 10건, 넘치면 `…외 N건`
   - 본문은 HTML이면 블록 태그만 줄바꿈으로 바꿔 텍스트로 만들고(`get_text("\n")`은 Knox의 `--- <b>Original Message</b> ---`를 세 줄로 쪼갠다), 회신 인용(Gmail `님이 작성:`/`wrote:`, Outlook `보낸 사람:`+`보낸 날짜:`, 네이버·Knox `Original Message`)을 '새로 쓴 내용'과 분리해 넘긴다. 스레드 마지막 메일 3000자 + 인용 1500자, 앞선 메일 800자. 입력이 15만 자를 넘으면 오래된 '중요 표시 없음' 스레드부터 미리보기만 남긴다
   - Gmail '중요' 표시는 참고로만 넘긴다. 이 메일함은 대부분 그룹 주소(bizops@ 등)로 받아 To 기준으로는 거를 수 없고, '중요' 표시는 인터넷지로 납부 결과·카드 이용내역·뉴스레터를 포함하고 AWS 지원 케이스 답변은 빠뜨린다. Notion 활동 알림(`notify@mail.notion.com`)은 Kanban 섹션과 겹치는데 프롬프트로 막아도 모델이 골라서 코드에서 제외한다
   - Bedrock 요약이 실패하면 `⚠️ 요약 생성 실패`와 Gmail 중요 표시 스레드 목록만, Gmail 조회가 실패하면 `⚠️ 중요 메일 요약 실패`만 표시하고 보고는 계속 나간다. 비용은 실행당 입력 2만~10만 토큰 (평일 4시간 구간 ≈ 2만)
3. **메일 초안 작성** — To에 본인 이메일이 직접 포함된 외부 발신 메일만 (절대 전송 금지, 초안만)
4. **Slack 결과 보고** — Notion 현황 + Gmail 요약 + 초안 목록 + 오늘의 액션 가이드
   - 첫 줄 `📅` 날짜·시각은 scheduler가 실행 시작 시각으로 넣는다 (Claude에 맡겼을 때 09:00 보고서에 `13:00 KST`를 적었다)

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
| `S3_BUCKET` | S3 Vectors 버킷 (gov-funding 벡터, 운영값 `gov-funding-monitor-snapshots`) | `gov-funding-monitor` |
| `AI_NEWS_S3_BUCKET` | S3 버킷 (ai-news 벡터) | S3_BUCKET과 동일 |
| `BEDROCK_REGION` | Bedrock 리전 | `us-west-2` |
| `BEDROCK_MODEL_ID` | Bedrock 모델 ID (크롤러 분석/요약) | `global.anthropic.claude-sonnet-5` |
| `EMBEDDING_MODEL_ID` | 임베딩 모델 ID | `amazon.titan-embed-text-v2:0` |
| `SLACK_BOT_TOKEN` | Slack Bot Token | (필수) |
| `SLACK_APP_TOKEN` | Slack App Token (Socket Mode) | (필수) |
| `SLACK_CHANNEL_ID` | gov-funding 알림 채널 | (필수) |
| `GOV_FUNDING_CHANNEL_ID` | gov-funding Q&A 채널 | SLACK_CHANNEL_ID와 동일 |
| `AI_NEWS_CHANNEL_ID` | AI 뉴스 알림/Q&A 채널 | (필수) |
| `GMAIL_CREDENTIALS` | Gmail API credentials JSON | (선택) |
| `GMAIL_MCP_CREDENTIALS_PATH` | scheduler 메일 자동 정리용 OAuth 클라이언트 | `/home/ubuntu/mcp/gmail-mcp-server/credentials.json` |
| `NOTION_TOKEN` | scheduler Kanban 섹션용 Notion integration 토큰 (Claude CLI Notion MCP와 같은 토큰) | (필수) |
| `GMAIL_MCP_TOKEN_PATH` | scheduler 메일 자동 정리용 OAuth 토큰 (refresh token 사용) | `/home/ubuntu/mcp/gmail-mcp-server/gmail_token.json` |
| `MAIL_DIGEST_MODEL_ID` | scheduler 중요 메일 요약 모델 (`BEDROCK_REGION` 사용) | `global.anthropic.claude-sonnet-5` |
| `EMAIL_RECIPIENTS` | 이메일 수신자 (콤마 구분) | (선택) |
| `RELEVANCE_THRESHOLD` | Bedrock 관련성 임계값 (운영 `.env` 0.7) | `0.6` |
| `GOV_FUNDING_RUN_ON_START` | 서비스 시작 시 1회 실행 (신규만 알림) | `1` |
| `GOV_FUNDING_FORCE_RESEND_ON_START` | 시작 시 기존 공고까지 전부 재발송 | `0` |
| `GOV_FUNDING_MAX_BEDROCK_CALLS` | 실행당 Bedrock 분석 상한 (초과분은 다음 실행) | `150` |
| `GOV_FUNDING_STATE_DIR` | Bedrock 탈락 캐시 디렉토리 | `/home/ubuntu/.local/state/gov-funding` |
| `GOV_FUNDING_RUN_TIMEOUT` | 1회 실행 제한 시간(초) | `7200` |
| `DEADLINE_ALERT_DAYS` | 마감 알림 기준일 | `7` |
| `AI_NEWS_IMPORTANCE_THRESHOLD` | AI 뉴스 중요도 임계값 | `0.5` |
| `AI_NEWS_MAX_PER_SOURCE` | 소스별 최대 기사 수 | `20` |
| `CLAUDE_CODE_USE_BEDROCK` | Claude Code Bedrock 사용 | `1` |
| `AWS_BEARER_TOKEN_BEDROCK` | Bedrock Bearer Token — 설정하지 않음 (Claude CLI가 EC2 IAM Role 사용, 위 "Claude CLI 인증" 참고) | (미설정) |
| `ANTHROPIC_API_PROVIDER` | API 프로바이더 | `bedrock` |
| `ANTHROPIC_SMALL_FAST_MODEL` | 빠른 모델 ID | (선택) |

## Configuration Constants

### gov-funding
키워드 단계는 넓게 통과시키고 Bedrock이 최종 판단한다 (`analyzers/relevance_filter.py`).
- **RELEVANCE_KEYWORDS** (53개): IT/SW, AI, 창업/스타트업, 지원(R&D, 바우처 등) + AX, DX, IoT, XR, 반도체, 정보보호/보안 등. 영문 약어는 대소문자 구분 + 앞 글자가 영문이 아닐 때만 (`AICT`, `AIoT`는 통과), IT/SW는 양쪽 경계 (`SWITCH`, `SUMMIT` 오탐 방지)
- **EXCLUDE_KEYWORDS** (하드) / **SOFT_EXCLUDE_KEYWORDS** (제조, 에너지, 바이오, 물류): 제목에 **STRONG_IT_KEYWORDS**(AI, SW, 데이터, 보안 등)가 있으면 적용하지 않는다 ('AI 제조 혁신' 통과). '제약'→'제약사/제약산업', '조선'→'조선업' (부분 문자열 오탐 방지)
- **NON_ACTIONABLE_PATTERNS**: 선정/평가 결과, 입법·행정예고, 하위법령 등 신청할 수 없는 공고는 IT 키워드가 있어도 제외. 조달/용역/주관기관 모집/설명회/해외 전시회는 유지
- **지역** (`classify_region`): ① 소관부처가 광역 지자체면 서울특별시/경기도만 허용 ② 제목 앞 태그(`[경기]`, `(충남)`)는 지역명일 때만 판단 (`[공고]`, `[코리아스타트업포럼]`은 무시) ③ '경기 광주'는 경기 ④ **EXCLUDE_REGIONS**(광역 전체·약칭, 호남권/충청권/대경권 등 권역, 대덕특구, 주요 시)가 있으면 제외 ⑤ **ALLOWED_REGIONS**(서울, 경기, 수도권, 성남, 판교 등)면 허용 ⑥ 지역 정보가 없으면 전국으로 허용. 부분 문자열 함정은 `REGION_ALIAS_PATTERNS`로 처리 (대전환, 부산물, 세종대왕 등). 입찰/조달 공고는 제목 속 지역이 수행 장소라 제목 지역 규칙을 건너뛴다
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

**재시작은 즉시 1회 워크플로우를 실행한다.** gov-funding `main()`은 시작 시 `execute_workflow(force_resend=False)`를 호출해 저장된 공고와 비교하므로 **신규 공고만** 알린다 (예전에는 `force_resend=True`라 재시작마다 기존 공고 수십 건을 재발송했다). 시작 실행을 끄려면 `GOV_FUNDING_RUN_ON_START=0`, 기존 공고까지 다시 보내려면 `GOV_FUNDING_FORCE_RESEND_ON_START=1`. 재시작으로 09:00 정각을 놓친 날은 이 시작 실행이 그날 실행을 대신한다 (APScheduler는 재시작 후 지난 시각을 다시 실행하지 않으므로 `GOV_FUNDING_RUN_ON_START=0`이면 그날은 건너뛴다). 1회 실행은 `GOV_FUNDING_RUN_TIMEOUT`(기본 2시간) 안에 끝나지 않으면 중단된다. ai-news `main()`은 스케줄 등록 후 `execute_workflow()`를 무조건 호출하므로 재시작하면 채널에 알림이 한 번 더 올라간다. 코드 수정 후 재시작 전에는 `--dry-run`으로 무엇이 발송될지 먼저 확인한다.

## Testing

```bash
# 서비스별 venv로 해당 테스트 디렉토리만 실행 (루트에서 `python -m pytest`를 돌리면
# 다른 서비스 의존성(feedparser, slack_bolt 등)이 없어 수집 단계에서 실패한다)
/home/ubuntu/venvs/gov-funding/bin/python -m pytest tests/gov_funding
/home/ubuntu/venvs/ai-news/bin/python -m pytest tests/ai_news
/home/ubuntu/venvs/slack-app/bin/python -m pytest tests/slack_app
/home/ubuntu/venvs/scheduler/bin/python -m pytest tests/test_scheduler.py tests/test_mail_cleanup.py tests/test_kanban.py tests/test_mail_digest.py

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
