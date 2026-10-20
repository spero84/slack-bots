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
    deadline_max_days: int = field(default_factory=lambda: int(os.environ.get("DEADLINE_MAX_DAYS", "61")))

    # Bedrock (us-west-2 리전 사용)
    bedrock_region: str = field(default_factory=lambda: os.environ.get("BEDROCK_REGION", "us-west-2"))
    bedrock_model_id: str = field(default_factory=lambda: os.environ.get(
        "BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5"
    ))


# 관련 키워드 필터 (1차 필터는 느슨하게 유지 — 최종 판단은 Bedrock)
# 한글 키워드는 부분 문자열로 찾는다.
# ASCII 키워드는 대소문자를 구분하고, 앞 글자가 영문자(숫자로 시작하는 키워드는 숫자)면 매칭하지 않는다.
#   예: AI는 'AICT', 'AIoT', 'AI융합'에는 매칭, 'RETAIL', 'TRAINING', 'BLOCKCHAIN'에는 매칭 안 됨
# ASCII_BOTH_BOUNDARY_KEYWORDS는 뒤 글자도 영문자가 아니어야 한다 (UNIT, ITEM, SUMMIT, SWISS, SWITCH 제외)
RELEVANCE_KEYWORDS = [
    # IT/SW 관련
    "IT", "ICT", "SW", "소프트웨어", "정보기술", "정보통신",
    "디지털", "플랫폼", "클라우드", "SaaS",

    # AI/디지털 전환 관련
    "AI", "인공지능", "머신러닝", "딥러닝", "데이터",
    "빅데이터", "자연어처리", "LLM", "생성형", "AX", "DX",

    # 신기술/융합 분야
    "IoT", "사물인터넷", "XR", "메타버스", "블록체인", "핀테크",
    "5G", "6G", "반도체", "로봇",

    # 보안 관련
    "정보보호", "정보보안", "사이버보안", "융합보안", "보안",

    # 창업/스타트업 관련
    "창업", "스타트업", "벤처", "예비창업", "초기창업",
    "성장", "스케일업", "액셀러레이터", "보육",

    # 지원 관련
    "R&D", "연구개발", "기술개발", "사업화",
    "바우처", "지원금", "보조금", "투자",
]

# 앞뒤 모두 영문자 경계가 필요한 ASCII 키워드
ASCII_BOTH_BOUNDARY_KEYWORDS = ["IT", "SW"]

# 강한 IT 신호 (제목에 있으면 제외 키워드를 적용하지 않음 — AI 제조/에너지 AI/바이오 데이터 등 융합 사업 보호)
STRONG_IT_KEYWORDS = [
    "AI", "AX", "DX", "SW", "ICT", "IoT", "SaaS",
    "인공지능", "소프트웨어", "데이터", "디지털", "클라우드",
    "정보보호", "보안", "플랫폼",
]

# 제외 키워드 (강) — 관련성 낮은 비IT 분야
# '제약'은 '업종 제약 없는' 같은 문장에, '조선'은 '조선대학교'/'조선일보'에 걸리므로 구체적인 형태만 쓴다
EXCLUDE_KEYWORDS = [
    # 1차산업/자원
    "농업", "축산", "수산", "임업", "광업",
    # 제조/중공업
    "제조업", "금형", "주조", "용접", "섬유", "의류",
    "건설", "토목", "조선업", "조선해양", "철강", "석유화학",
    # 에너지/환경
    "신재생에너지", "태양광", "풍력", "원자력",
    # 바이오/의료
    "의료기기", "제약사", "제약산업",
    # 기타 비IT
    "관광", "숙박", "외식", "요식업", "운송",
]

# 제외 키워드 (약) — AI/IT 융합 사업이 많은 분야
SOFT_EXCLUDE_KEYWORDS = [
    "제조", "에너지", "바이오", "물류",
]

# 한글 키워드 오탐 방지 정규식 (없으면 부분 문자열 매칭)
KEYWORD_PATTERNS = {
    "제조": r"(?<!경)제조",      # 사회연대경제조직
    "관광": r"(?<!체육)관광",    # 문화체육관광부
}

# 지원 신청이 불가능한 공고 (결과 발표, 입법/행정예고, 마감 표시) — 정규식, 제목+분야에 적용
# IT 신호와 무관하게 항상 제외한다.
# 입찰/용역, 주관기관 모집, 설명회, 해외 전시회는 의도적으로 제외하지 않는다 (제품 결정)
NON_ACTIONABLE_PATTERNS = [
    r"(선정|평가|심사|최종|서류|발표)\s*결과",
    r"결과\s*(공고|안내|발표|알림)",
    r"입법\s*예고",
    r"행정\s*예고",
    r"하위\s*법령",            # 'AI기본법 시행 및 하위법령 공개'
    r"합격자\s*(발표|공고)",
    r"대상자\s*선정\s*(공고|안내|결과)",
    # 마감 표시 태그: [접수마감] [모집마감] [마감] (마감) 【마감】 (조기마감) — 괄호 안이 마감 표시뿐일 때만
    # '(~4/5 마감)', '(선착순마감)', '[마감임박]', '(마감일 연장)'은 해당하지 않는다
    r"[\[(【〔<]\s*(?:접수|모집|신청|공고|조기)?\s*마감(?:됨|완료)?\s*[\])】〕>]",
    # 괄호 없는 '조기 마감' — '소진 시 조기마감', '조기 마감될 수 있음' 같은 안내 문구는 제외
    r"(?<!시 )(?<!시)조기\s*마감(?!\s*(?:될|되|시|가능|예정|할|주의|우려|있))",
]

# 허용 지역 (서울/경기). 지역 정보가 없는 공고(전국 대상)도 포함
# '수도권'은 '비수도권'과 구분해서 매칭한다 (REGION_ALIAS_PATTERNS)
ALLOWED_REGIONS = [
    "서울", "경기", "수도권",
    "성남", "판교", "분당", "수원", "용인", "고양", "과천", "안산", "화성",
]

# 제외 지역 (광역/도 정식·약칭, 권역, 특구, 주요 시·군)
# 오탐 방지가 필요한 이름은 REGION_ALIAS_PATTERNS에서 정규식으로 매칭한다
# 단독 '광주'는 경기 광주시와 구분이 필요해 REGION_AMBIGUOUS_PATTERNS에서 처리한다
EXCLUDE_REGIONS = [
    # 광역시/특별자치시
    "부산", "대구", "인천", "광주광역시", "대전", "울산", "세종",
    # 도 (약칭 + 정식 명칭 — '충남'은 '충청남도'의 부분 문자열이 아니다)
    "강원", "충북", "충청북도", "충남", "충청남도",
    "전북", "전라북도", "전남", "전라남도", "전남광주",
    "경북", "경상북도", "경남", "경상남도", "제주",
    # 특구
    "대덕특구", "대덕연구개발특구",
    # 시/군
    "구미", "포항", "창원", "진주", "김해", "거제",
    "천안", "아산", "청주", "충주", "전주", "군산", "익산",
    "목포", "여수", "순천", "안동", "경주", "춘천", "원주", "강릉", "홍성",
    # 권역
    "호남권", "영남권", "충청권", "대경권", "강원권", "제주권",
    "경상권", "전라권", "부울경", "비수도권",
]

# 지역명 오탐 방지 정규식 (없으면 부분 문자열 매칭)
REGION_ALIAS_PATTERNS = {
    "수도권": r"(?<!비)수도권",
    "부산": r"부산(?!물)",                                   # 부산물
    "대전": r"(?<![가-힣])대전(?!환)",                        # AI 대전환, 소프트웨어대전(전시회)
    "세종": r"세종(?=특별자치시|시|창조경제|테크노파크|TP)",   # 세종대왕/세종학당/세종대학교 (서울)
    "아산": r"아산(?!나눔|병원|재단|의료원|사회복지|정책)",     # 아산나눔재단, 서울아산병원
    "전주": r"(?<![가-힣])전주(?!기)",                        # 전주기(全週期), 안전주간
    "여수": r"여수(?!신)",                                   # 여수신
    "경주": r"(?<![가-힣])경주(?!마|대회|용)",                # 자동차 경주
    "원주": r"원주(?!민)",                                   # 원주민
}

# 다른 지역 표시가 없을 때만 제외하는 모호한 지역명
# - 단독 '광주': 경기 광주시일 수 있음 ('경기 광주(시)'는 미리 '경기'로 치환)
# - '동남권'/'동북권': 부울경·대경 권역이지만 서울 생활권 이름이기도 하다
REGION_AMBIGUOUS_PATTERNS = [
    r"(?<![가-힣])광주(?!광역시)",
    r"동남권",
    r"동북권",
]

# 제목 맨 앞 [태그] 안에서는 짧은 이름도 지역으로 본다 ('[세종]', '[광주]')
REGION_TAG_ONLY_ALIASES = ["세종", "광주"]

# 조달/입찰 공고(분야 기준)는 제목의 지역명이 지원 자격이 아니라 사업 수행 장소이므로 제목 지역 규칙을 적용하지 않는다
# 예: NIA 조달입찰공고 '경상북도 지방소멸 위기 극복을 위한 주민케어 AI 서비스'
REGION_EXEMPT_CATEGORY_PATTERN = r"입찰|조달|사전\s*규격|사전\s*공개"

# 크롤링 대상 사이트
CRAWL_SOURCES = {
    "kstartup": {
        "name": "K-Startup",
        "base_url": "https://www.k-startup.go.kr",
        "list_url": "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do",
        "detail_url_template": "https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do?schM=view&pbancSn={id}",
        # 마감된 공고는 모집중 경로에서 빈 페이지가 나오므로 상세 조회 시 이 경로로 재시도
        "closed_detail_url_template": "https://www.k-startup.go.kr/web/contents/bizpbanc-deadline.do?schM=view&pbancSn={id}",
        # 탭: PBC010 중앙부처·지자체·공공기관, PBC020 민간기관·교육기관
        "tabs": ["PBC010", "PBC020"],
        # 지원지역 서버 필터 (LC00 전국, LC01 서울, LC08 경기) - ALLOWED_REGIONS 정책과 동일
        "region_codes": ["LC00", "LC01", "LC08"],
        # 탭별 최대 페이지 (페이지당 15건, 2026-10 기준 지역 필터 적용 시 PBC010 8페이지, PBC020 4페이지)
        "max_pages": 20,
        # 요청 간 대기 (초)
        "page_delay": 0.7,
    },
    "bizinfo": {
        "name": "기업마당",
        "base_url": "https://www.bizinfo.go.kr",
        "list_url": "https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do",
        "detail_url_template": "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?pblancId={id}",
        # 등록일 기준 최근 N일 공고만 수집 (주말/연휴 포함 여유분)
        "lookback_days": 5,
        # 페이지당 15건 고정, 영업일 하루 약 2~3페이지
        "max_pages": 15,
    },
    "nipa": {
        "name": "NIPA",
        "base_url": "https://www.nipa.kr",
        "list_url": "https://www.nipa.kr/home/2-2",
        "detail_url_template": "https://www.nipa.kr/home/2-2/{id}",
        # 요청 간 대기 (초)
        "page_delay": 0.7,
        # 목록 최대 페이지 (페이지당 10건) — curPage가 무시돼도 무한 반복하지 않도록 상한
        "max_pages": 10,
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
                # 일반 공지 게시판 (별도 '사업공고' 게시판은 없음)
                "name": "공지사항",
                "cbIdx": "99835",
                "list_url": "https://www.nia.or.kr/site/nia_kor/ex/bbs/List.do?cbIdx=99835",
            },
        ],
        "detail_url_template": "https://www.nia.or.kr/site/nia_kor/ex/bbs/View.do?cbIdx={cbIdx}&bcIdx={bcIdx}&parentSeq={parentSeq}",
        # 지원 대상이 아닌 글 (제목 앞 [카테고리] 기준 / 제목 포함 문자열 기준)
        "skip_categories": ["평가결과 공개"],
        "skip_title_keywords": ["취소공고"],
        # 게시판별 최대 페이지 (페이지당 10건)
        "max_pages": 5,
        # 요청 간 대기 (초)
        "page_delay": 0.7,
    },
    "iitp": {
        # www.iitp.kr robots.txt가 'Disallow: /' → 범부처통합연구지원시스템(IRIS)의 IITP 사업공고를 수집
        "name": "IITP",
        "base_url": "https://www.iris.go.kr",
        # 목록 화면 (Referer)
        "list_url": "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituListView.do",
        # 목록 JSON (POST form)
        "list_api_url": "https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do",
        "detail_page_url": "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do",
        "detail_url_template": "https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={id}&ancmPrg={prg}",
        "file_download_url": "https://www.iris.go.kr/comm/file/fileDownload.do",
        # 전문기관: 정보통신기획평가원
        "sorgn_id": "10004",
        # 접수중, 접수예정 (ancmEnd=마감은 제외)
        "ancm_prgs": ["ancmIng", "ancmPre"],
        # 상태별 최대 페이지 (페이지당 10건 고정)
        "max_pages": 5,
        # 목록 요청 재시도 대기 (초) — ConnectionError/Timeout/5xx, 최대 3회 시도
        "retry_delays": [10, 60],
    },
    "jointips": {
        # 2026-09 CMS 이전으로 gnuboard URL(bbs/board.php)은 404 → 공개 JSON API 사용
        # (www.jointips.or.kr은 apex 도메인으로 301)
        "name": "JOINTIPS",
        "base_url": "https://jointips.or.kr",
        "list_api_url": "https://jointips.or.kr/api/cms/public/event/list",
        "detail_api_url": "https://jointips.or.kr/api/cms/public/event/{id}",
        "attach_api_url": "https://jointips.or.kr/api/cms/store/list-by-path",
        "file_download_url_template": "https://jointips.or.kr/api/cms/store/download/{id}",
        "detail_url_template": "https://jointips.or.kr/contents/events/detail?eventId={id}",
        "site_id": 4,
        # 목록 페이지 크기 (최신 등록순)
        "page_size": 50,
        "max_pages": 3,
    },
    "msit": {
        "name": "과학기술정보통신부",
        "base_url": "https://www.msit.go.kr",
        "list_url": "https://www.msit.go.kr/bbs/list.do?sCode=user&mPid=121&mId=311",
        # bbsSeqNo=100(게시판 ID)이 없으면 HTTP 200 '시스템 점검 안내' 오류 페이지가 온다
        "detail_url_template": "https://www.msit.go.kr/bbs/view.do?sCode=user&mPid=121&mId=311&bbsSeqNo=100&nttSeqNo={id}",
        # 첨부파일 링크는 onclick="fn_download('{atchFileNo}', '{fileOrd}', 'hwpx')" — 일반 GET으로 다운로드 가능
        "attachment_url_template": "https://www.msit.go.kr/ssm/file/fileDown.do?atchFileNo={atch_file_no}&fileOrd={file_ord}&fileBtn=A",
    },
    "mss": {
        "name": "중소벤처기업부",
        "base_url": "https://www.mss.go.kr",
        "list_url": "https://www.mss.go.kr/site/smba/ex/bbs/List.do?cbIdx=310",
        "detail_url_template": "https://www.mss.go.kr/site/smba/ex/bbs/View.do?cbIdx=310&bcIdx={bcIdx}&parentSeq={parentSeq}",
        # 목록 최대 페이지 (페이지당 10건) — pageIndex가 무시돼도 무한 반복하지 않도록 상한
        "max_pages": 10,
        # 목록 페이지 요청 간 대기 (초)
        "page_delay": 1.0,
    },
    "motie": {
        "name": "산업통상자원부",
        # 부처 명칭 변경으로 motie.go.kr → motir.go.kr 301 리다이렉트 (경로 유지)
        "base_url": "https://www.motir.go.kr",
        "list_url": "https://www.motir.go.kr/kor/article/ATCL2826a2625",
        # '/view'가 없으면 404. 목록 링크의 '?mno=&pageIndex=N'은 붙이지 않는다 (같은 페이지)
        "detail_url_template": "https://www.motir.go.kr/kor/article/ATCL2826a2625/{id}/view",
    },
}


def get_config() -> Config:
    """설정 인스턴스 반환"""
    return Config()
