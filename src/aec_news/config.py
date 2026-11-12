"""AEC News 설정 — 언론사 목록, 검색어, 환경변수

언론사/검색어는 2026-11-12 Google News RSS 실측으로 정했다 (CLAUDE.md 'aec-news' 참고).
"""
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from .models import Region, Theme

KST = ZoneInfo("Asia/Seoul")


@dataclass
class Config:
    """환경변수 기반 설정"""

    slack_bot_token: str = field(default_factory=lambda: os.environ.get("SLACK_BOT_TOKEN", ""))
    slack_channel_id: str = field(default_factory=lambda: os.environ.get("AEC_NEWS_CHANNEL_ID", ""))

    bedrock_region: str = field(default_factory=lambda: os.environ.get("BEDROCK_REGION", "us-west-2"))
    bedrock_model_id: str = field(default_factory=lambda: os.environ.get(
        "AEC_NEWS_MODEL_ID", os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5")
    ))

    # 관련성 임계값 (ai_tech 기사 score). 기본 0.6 — 정책·전망 기사(0.5~0.6)는 경계
    relevance_threshold: float = field(default_factory=lambda: float(
        os.environ.get("AEC_NEWS_RELEVANCE_THRESHOLD", "0.6")
    ))
    # 📎 참고 섹션 (건설사의 AI 데이터센터 수주 등) 표시 여부와 최대 건수
    include_infra: bool = field(default_factory=lambda: os.environ.get("AEC_NEWS_INCLUDE_INFRA", "1") == "1")
    max_infra_items: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_MAX_INFRA", "8")))

    # 수집 기간(일): 국내는 하루 3~4건이라 2일(전날 실행과 하루 겹침 — 늦게 색인된 기사도 잡고, 중복은 전송 이력이 막는다).
    # 미국·싱가포르 주요 매체는 주 1~2건이라 7일. 마지막 성공 이후 더 지났으면 그만큼 늘린다 (최대 7일)
    kr_lookback_days: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_KR_DAYS", "2")))
    intl_lookback_days: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_INTL_DAYS", "7")))
    max_lookback_days: int = 7

    # 스케줄 (KST)
    schedule_hour: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_HOUR", "8")))
    schedule_minute: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_MINUTE", "30")))
    run_on_start: bool = field(default_factory=lambda: os.environ.get("AEC_NEWS_RUN_ON_START", "0") == "1")

    # Google News 요청 간격(초, 0~1초 지터 추가). 2026-11-12 실측: 2~3초 간격 수십 회는 문제없었고,
    # 두 프로세스가 동시에 ~3초 간격으로 보낼 때 429(google.com/sorry)가 한 번 났다
    request_interval: float = field(default_factory=lambda: float(os.environ.get("AEC_NEWS_REQUEST_INTERVAL", "3.0")))
    max_decodes: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_MAX_DECODES", "30")))


def get_config() -> Config:
    return Config()


@dataclass(frozen=True)
class Outlet:
    """검색 대상 언론사"""
    key: str
    name: str
    region: Region
    domain: str                                   # site: 연산자에 넣는 도메인 (하위 도메인까지 매칭된다)
    keep_hosts: tuple[str, ...]                   # 받아들이는 호스트 (정확히 일치). 나머지 하위 도메인은 버린다
    host_names: tuple[tuple[str, str], ...] = ()  # 계열 매체 표시 이름 (it.chosun.com → IT조선)

    def name_for(self, host: str) -> str:
        return dict(self.host_names).get((host or "").lower(), self.name)


# 순서 = 같은 소식 묶음에서 대표 기사를 고를 때의 우선순위(점수·시각이 같을 때)이자 site: 묶음 순서.
# keep_hosts: Google News <source url>의 호스트가 이 집합에 정확히 있어야 한다 (site:는 하위 도메인까지 매칭하므로
# 포럼·사전·시세·광고 하위 도메인이 섞인다 — cbiz.chosun.com '속보서비스', mlbpark.donga.com, dic.hankyung.com,
# stock.mk.co.kr(연합 재배포), view.mk.co.kr(더구루), councils.forbes.com, sponsored.bloomberg.com, *.usatoday.com 등).
# 영문판(en.yna.co.kr, en.sedaily.com)도 버린다 — 국내 기사가 미국판 검색에 영어로 중복된다.
_OUTLET_LIST: list[Outlet] = [
    # 🇰🇷 국내 10
    Outlet("chosun", "조선일보", Region.KR, "chosun.com",
           ("www.chosun.com", "biz.chosun.com", "it.chosun.com", "realty.chosun.com"),
           (("biz.chosun.com", "조선비즈"), ("it.chosun.com", "IT조선"), ("realty.chosun.com", "땅집고"))),
    Outlet("joongang", "중앙일보", Region.KR, "joongang.co.kr", ("www.joongang.co.kr",)),
    Outlet("donga", "동아일보", Region.KR, "donga.com",
           ("www.donga.com", "it.donga.com", "dbr.donga.com", "bizn.donga.com", "weekly.donga.com"),
           (("it.donga.com", "IT동아"), ("dbr.donga.com", "동아비즈니스리뷰"), ("bizn.donga.com", "비즈N"),
            ("weekly.donga.com", "주간동아"))),
    Outlet("hani", "한겨레", Region.KR, "hani.co.kr", ("www.hani.co.kr", "h21.hani.co.kr"),
           (("h21.hani.co.kr", "한겨레21"),)),
    Outlet("khan", "경향신문", Region.KR, "khan.co.kr", ("www.khan.co.kr",)),
    Outlet("yna", "연합뉴스", Region.KR, "yna.co.kr", ("www.yna.co.kr",)),
    Outlet("hankyung", "한국경제", Region.KR, "hankyung.com",
           ("www.hankyung.com", "magazine.hankyung.com", "plus.hankyung.com"),
           (("magazine.hankyung.com", "한경매거진"),)),
    Outlet("mk", "매일경제", Region.KR, "mk.co.kr", ("www.mk.co.kr", "m.mk.co.kr", "re.mk.co.kr")),
    Outlet("sedaily", "서울경제", Region.KR, "sedaily.com", ("www.sedaily.com", "m.sedaily.com")),
    Outlet("mt", "머니투데이", Region.KR, "mt.co.kr", ("www.mt.co.kr", "news.mt.co.kr")),
    # 🇺🇸 미국 10
    Outlet("nytimes", "The New York Times", Region.US, "nytimes.com", ("www.nytimes.com",)),
    Outlet("wsj", "The Wall Street Journal", Region.US, "wsj.com", ("www.wsj.com",)),
    Outlet("wapo", "The Washington Post", Region.US, "washingtonpost.com", ("www.washingtonpost.com",)),
    Outlet("reuters", "Reuters", Region.US, "reuters.com", ("www.reuters.com",)),
    Outlet("bloomberg", "Bloomberg", Region.US, "bloomberg.com", ("www.bloomberg.com",)),
    Outlet("apnews", "AP News", Region.US, "apnews.com", ("apnews.com", "www.apnews.com")),
    Outlet("cnbc", "CNBC", Region.US, "cnbc.com", ("www.cnbc.com",)),
    Outlet("cnn", "CNN", Region.US, "cnn.com", ("www.cnn.com", "edition.cnn.com")),
    Outlet("forbes", "Forbes", Region.US, "forbes.com", ("www.forbes.com",)),
    Outlet("usatoday", "USA Today", Region.US, "usatoday.com", ("www.usatoday.com",)),
    # 🇸🇬 싱가포르 4 (TODAY는 CNA로 통합돼 제목이 비어 있어 제외)
    Outlet("straitstimes", "The Straits Times", Region.SG, "straitstimes.com", ("www.straitstimes.com",)),
    Outlet("businesstimes", "The Business Times", Region.SG, "businesstimes.com.sg", ("www.businesstimes.com.sg",)),
    Outlet("cna", "CNA", Region.SG, "channelnewsasia.com", ("www.channelnewsasia.com",)),
    Outlet("theedge", "The Edge Singapore", Region.SG, "theedgesingapore.com", ("www.theedgesingapore.com",)),
]

OUTLETS: dict[str, Outlet] = {o.key: o for o in _OUTLET_LIST}

# 원문 URL 경로가 이것으로 시작하면 기사 아님 (회사 프로필, 시세, 영상, 보도자료, 광고)
DROP_PATH_PREFIXES = ("/profile/", "/latest/", "/company/", "/companies/", "/quote/", "/videos/",
                      "/news/videos/", "/press-release", "/sponsored/")


@dataclass(frozen=True)
class RegionSearch:
    """Google News 지역판 파라미터"""
    hl: str
    gl: str
    ceid: str


REGION_SEARCH = {
    Region.KR: RegionSearch(hl="ko", gl="KR", ceid="KR:ko"),
    Region.US: RegionSearch(hl="en-US", gl="US", ceid="US:en"),
    Region.SG: RegionSearch(hl="en-SG", gl="SG", ceid="SG:en"),   # en-US판으로 찾으면 더 오래된 기사가 섞인다
}

# 검색어 (주제 묶음 × 국가). 실제 쿼리는 `{검색어} (site:a OR site:b ...) when:Nd`
# - intitle:을 붙인다. 없으면 사이드바·관련 기사 텍스트까지 매칭돼 결과가 거의 전부 노이즈다
# - 따옴표("EPC")는 결과를 바꾸지 않는다. 'EPC/플랜트/엔지니어링'은 실측 기간에 추가 적중이 없었지만 주제 범위라 둔다
# - 'AEC'는 국내 기사에서 반도체 케이블·X-ray 노출 제어라 국내 검색어에서 뺐다
# - 판정은 Bedrock이 다시 하므로 검색어는 넓게 잡는다
SEARCH_TERMS: dict[str, dict[Region, str]] = {
    "drawing": {
        Region.KR: "(intitle:도면 OR intitle:BIM OR intitle:디지털트윈 OR intitle:CAD)",
        Region.US: '(intitle:BIM OR intitle:blueprints OR intitle:"construction drawings" OR intitle:"engineering drawings")',
        Region.SG: '(intitle:BIM OR intitle:blueprints OR intitle:"construction drawings" OR intitle:"engineering drawings")',
    },
    "aec_ai": {
        Region.KR: "intitle:AI (intitle:건설 OR intitle:건축 OR intitle:플랜트 OR intitle:EPC OR intitle:엔지니어링)",
        Region.US: "intitle:AI (intitle:construction OR intitle:contractors OR intitle:architects OR intitle:AEC OR intitle:EPC)",
        Region.SG: 'intitle:AI (intitle:construction OR intitle:BIM OR intitle:"built environment")',
    },
    "rag": {
        Region.KR: "RAG (건설 OR 플랜트 OR 엔지니어링 OR 도면 OR BIM)",
        Region.US: '"retrieval-augmented" (construction OR engineering OR BIM OR EPC)',
        Region.SG: '"retrieval-augmented" (construction OR engineering OR BIM OR EPC)',
    },
}
SEARCH_KIND_THEME = {"drawing": Theme.DRAWING_BIM, "aec_ai": Theme.AEC_AI, "rag": Theme.AEC_RAG}

# 점검 쿼리: 지역마다 1회. 결과가 0건이면 Google News 응답 이상으로 보고 경고한다
# (주제 쿼리는 실제로 0건인 날이 많아 0건 경고를 하지 않는다)
HEALTH_TERMS: dict[Region, tuple[str, tuple[str, ...]]] = {
    Region.KR: ("intitle:AI", ("chosun", "donga", "mk")),
    Region.US: ("intitle:AI", ("reuters", "cnbc", "bloomberg")),
    Region.SG: ("intitle:AI", ("straitstimes", "cna")),
}

# Google News는 q가 200자를 넘으면 뒤를 조용히 잘라 when:이 빠진다 (2026-11-12 실측: 200자 정상, 201자부터 무시).
# 여유를 두고 190자로 묶는다.
MAX_QUERY_CHARS = 190
