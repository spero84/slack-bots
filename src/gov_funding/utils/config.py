"""환경 설정 및 상수 정의"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    """애플리케이션 설정"""

    # AWS
    s3_bucket: str = field(default_factory=lambda: os.environ.get("S3_BUCKET", "gov-funding-monitor"))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "ap-northeast-2"))

    # Slack
    slack_bot_token: str = field(default_factory=lambda: os.environ.get("SLACK_BOT_TOKEN", ""))
    slack_channel_id: str = field(default_factory=lambda: os.environ.get("SLACK_CHANNEL_ID", ""))

    # Gmail
    gmail_credentials: str = field(default_factory=lambda: os.environ.get("GMAIL_CREDENTIALS", ""))
    email_recipients: list[str] = field(default_factory=lambda: [
        e.strip() for e in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if e.strip()
    ])

    # Filtering
    relevance_threshold: float = field(default_factory=lambda: float(os.environ.get("RELEVANCE_THRESHOLD", "0.6")))
    deadline_alert_days: int = field(default_factory=lambda: int(os.environ.get("DEADLINE_ALERT_DAYS", "7")))

    # Bedrock (us-west-2 리전 사용)
    bedrock_region: str = field(default_factory=lambda: os.environ.get("BEDROCK_REGION", "us-west-2"))
    bedrock_model_id: str = field(default_factory=lambda: os.environ.get(
        "BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1"
    ))


# 관련 키워드 필터
RELEVANCE_KEYWORDS = [
    # IT/SW 관련
    "IT", "ICT", "SW", "소프트웨어", "정보기술", "정보통신",
    "디지털", "플랫폼", "클라우드", "SaaS",

    # AI 관련
    "AI", "인공지능", "머신러닝", "딥러닝", "데이터",
    "빅데이터", "자연어처리", "LLM", "생성형",

    # 창업/스타트업 관련
    "창업", "스타트업", "벤처", "예비창업", "초기창업",
    "성장", "스케일업", "액셀러레이터", "보육",

    # 지원 관련
    "R&D", "연구개발", "기술개발", "사업화",
    "바우처", "지원금", "보조금", "투자",
]

# 제외 키워드 (관련성 낮은 분야)
EXCLUDE_KEYWORDS = [
    # 1차산업/자원
    "농업", "축산", "수산", "임업", "광업",
    # 제조/중공업
    "제조업", "제조", "금형", "주조", "용접", "섬유", "의류",
    "건설", "토목", "조선", "철강", "석유화학",
    # 에너지/환경
    "에너지", "신재생에너지", "태양광", "풍력", "원자력",
    # 바이오/의료 (IT 융합 제외 목적)
    "의료기기", "제약", "바이오",
    # 기타 비IT
    "관광", "숙박", "외식", "요식업", "물류", "운송",
]

# 허용 지역 (이 지역만 포함, 나머지는 제외)
# 지역 정보가 없는 공고(전국 대상)는 포함
ALLOWED_REGIONS = [
    "서울", "경기", "성남",
]

# 제외 지역 (명시적으로 제외할 지역)
EXCLUDE_REGIONS = [
    "부산", "대구", "인천", "광주", "대전", "울산", "세종",
    "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주",
    "구미", "포항", "창원", "진주", "김해", "거제",
]

# 크롤링 대상 사이트
CRAWL_SOURCES = {
    "kstartup": {
        "name": "K-Startup",
        "base_url": "https://www.k-startup.go.kr",
        "list_url": "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do",
        "detail_url_template": "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?schM=view&pbancSn={id}",
    },
    "bizinfo": {
        "name": "기업마당",
        "base_url": "https://www.bizinfo.go.kr",
        "list_url": "https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/list.do",
        "detail_url_template": "https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/view.do?pblancId={id}",
    },
    "nipa": {
        "name": "NIPA",
        "base_url": "https://www.nipa.kr",
        "list_url": "https://www.nipa.kr/home/2-2",
        "detail_url_template": "https://www.nipa.kr/home/2-2/{id}",
    },
    "nia": {
        "name": "NIA",
        "base_url": "https://www.nia.or.kr",
        "boards": [
            {
                "name": "입찰공고",
                "cbIdx": "78336",
                "list_url": "https://www.nia.or.kr/site/nia_kor/ex/bbs/List.do?cbIdx=78336",
            },
            {
                "name": "사업공고",
                "cbIdx": "99835",
                "list_url": "https://www.nia.or.kr/site/nia_kor/ex/bbs/List.do?cbIdx=99835",
            },
        ],
        "detail_url_template": "https://www.nia.or.kr/site/nia_kor/ex/bbs/View.do?cbIdx={cbIdx}&bcIdx={bcIdx}&parentSeq={parentSeq}",
    },
    "iitp": {
        "name": "IITP",
        "base_url": "https://www.iitp.kr",
        "list_url": "https://www.iitp.kr/web/lay1/program/S1T44C51/iris/list.do",
        "detail_url_template": "https://www.iitp.kr/web/lay1/program/S1T44C51/iris/view.do?seq={id}",
    },
}


def get_config() -> Config:
    """설정 인스턴스 반환"""
    return Config()
