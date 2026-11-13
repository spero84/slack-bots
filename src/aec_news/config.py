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

    # 경쟁사 검색 기간(일)과 실행당 최대 소식 수 — 같은 소식 묶음 단위 (넘치면 최신순으로 자르고 나머지는 다음 실행에서)
    competitor_lookback_days: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_COMPETITOR_DAYS", "7")))
    max_competitor_items: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_MAX_COMPETITOR", "15")))
    include_competitors: bool = field(default_factory=lambda: os.environ.get("AEC_NEWS_COMPETITORS", "1") == "1")
    # 제목에 경쟁사 이름이 없는 후보는 원문을 받아 본문을 확인한다 (원문 URL 복원에 Google 요청 2회씩) — 실행당 상한
    max_body_checks: int = field(default_factory=lambda: int(os.environ.get("AEC_NEWS_MAX_BODY_CHECKS", "15")))
    competitor_threshold: float = field(default_factory=lambda: float(
        os.environ.get("AEC_NEWS_COMPETITOR_THRESHOLD", "0.5")
    ))

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
    edition: str = ""                             # Google News 지역판 (EDITIONS 키). 비우면 섹션 기본값

    def name_for(self, host: str) -> str:
        return dict(self.host_names).get((host or "").lower(), self.name)

    @property
    def search_edition(self) -> str:
        return self.edition or DEFAULT_EDITION[self.region]


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
    # --- 해외 30곳: Press Gazette 'Top 50 English-language news sites in the world' (Similarweb 2026년 10월 방문 수,
    # 2026-11-11 발행) 순위 기준. 포털·애그리게이터(MSN, Yahoo, Google News), 플랫폼(Substack), 연예(People),
    # 실측 품질이 낮은 oneindia는 제외. 기존 미국 10곳은 모두 순위 안이라 유지(Bloomberg는 46위지만 EPC·인프라
    # 거래 기사 비중이 커서 유지 — 엄격히 순위대로면 33위 The Independent 자리다). 싱가포르 4곳은 사용자 요청으로 유지.
    # 줄 끝 숫자 = 순위. 지역판: 영국은 en-GB, 인도는 en-IN(en-US판보다 기사가 많고 최신), 그 밖은 en-US.
    # 🇺🇸 미국 14
    Outlet("nytimes", "The New York Times", Region.US, "nytimes.com", ("www.nytimes.com",)),            # 2
    Outlet("cnn", "CNN", Region.US, "cnn.com", ("www.cnn.com", "edition.cnn.com")),                       # 4
    Outlet("foxnews", "Fox News", Region.US, "foxnews.com", ("www.foxnews.com",)),                        # 8
    Outlet("cnbc", "CNBC", Region.US, "cnbc.com", ("www.cnbc.com",)),                                     # 15
    Outlet("nypost", "New York Post", Region.US, "nypost.com", ("nypost.com",)),                          # 16
    Outlet("reuters", "Reuters", Region.US, "reuters.com", ("www.reuters.com",)),                         # 17
    Outlet("apnews", "AP News", Region.US, "apnews.com", ("apnews.com", "www.apnews.com")),               # 18
    Outlet("usatoday", "USA Today", Region.US, "usatoday.com", ("www.usatoday.com",)),                    # 19
    Outlet("wsj", "The Wall Street Journal", Region.US, "wsj.com", ("www.wsj.com",)),                     # 20
    Outlet("nbcnews", "NBC News", Region.US, "nbcnews.com", ("www.nbcnews.com",)),                        # 22
    Outlet("wapo", "The Washington Post", Region.US, "washingtonpost.com", ("www.washingtonpost.com",)),  # 23
    Outlet("cbsnews", "CBS News", Region.US, "cbsnews.com", ("www.cbsnews.com",)),                        # 26
    Outlet("forbes", "Forbes", Region.US, "forbes.com", ("www.forbes.com",)),                             # 32
    Outlet("bloomberg", "Bloomberg", Region.US, "bloomberg.com", ("www.bloomberg.com",)),                 # 46
    # 🇸🇬 싱가포르 4 (TODAY는 CNA로 통합돼 제목이 비어 있어 제외)
    Outlet("straitstimes", "The Straits Times", Region.SG, "straitstimes.com", ("www.straitstimes.com",)),
    Outlet("businesstimes", "The Business Times", Region.SG, "businesstimes.com.sg", ("www.businesstimes.com.sg",)),
    Outlet("cna", "CNA", Region.SG, "channelnewsasia.com", ("www.channelnewsasia.com",)),
    Outlet("theedge", "The Edge Singapore", Region.SG, "theedgesingapore.com", ("www.theedgesingapore.com",)),
    # 🌐 글로벌 12
    Outlet("bbc", "BBC News", Region.GLOBAL, "bbc.com", ("www.bbc.com",), edition="en-GB"),               # 1
    Outlet("guardian", "The Guardian", Region.GLOBAL, "theguardian.com", ("www.theguardian.com",),
           edition="en-GB"),                                                                             # 6
    Outlet("dailymail", "Daily Mail", Region.GLOBAL, "dailymail.com", ("www.dailymail.com",),
           edition="en-GB"),                                                                             # 9
    # indiatimes.com은 여러 매체가 한 도메인을 쓴다 (ET 업종별 버티컬 20여 곳은 태그 페이지가 많아 제외)
    Outlet("indiatimes", "The Times of India", Region.GLOBAL, "indiatimes.com",
           ("timesofindia.indiatimes.com", "economictimes.indiatimes.com"),
           (("economictimes.indiatimes.com", "The Economic Times"),), edition="en-IN"),                 # 5
    Outlet("ndtv", "NDTV", Region.GLOBAL, "ndtv.com", ("www.ndtv.com",), edition="en-IN"),               # 13
    Outlet("hindustantimes", "Hindustan Times", Region.GLOBAL, "hindustantimes.com",
           ("www.hindustantimes.com",), edition="en-IN"),                                                # 21
    Outlet("indianexpress", "The Indian Express", Region.GLOBAL, "indianexpress.com",
           ("indianexpress.com",), edition="en-IN"),                                                     # 27
    Outlet("news18", "News18", Region.GLOBAL, "news18.com", ("www.news18.com",), edition="en-IN"),       # 28
    Outlet("thehindu", "The Hindu", Region.GLOBAL, "thehindu.com", ("www.thehindu.com",), edition="en-IN"),  # 30
    Outlet("cbc", "CBC News", Region.GLOBAL, "cbc.ca", ("www.cbc.ca",)),                                # 24
    Outlet("abcau", "ABC News (Australia)", Region.GLOBAL, "abc.net.au", ("www.abc.net.au",)),          # 31
    # Al Jazeera는 en-GB·en-US 결과가 같아(12/12) en-US 묶음에 넣는다 (en-GB 묶음은 BBC·Guardian·Daily Mail로 이미 참)
    Outlet("aljazeera", "Al Jazeera", Region.GLOBAL, "aljazeera.com", ("www.aljazeera.com",)),          # 25
]

OUTLETS: dict[str, Outlet] = {o.key: o for o in _OUTLET_LIST}

# 원문 URL 경로가 이것으로 시작하면 기사 아님 (회사 프로필, 시세, 영상, 보도자료, 광고)
DROP_PATH_PREFIXES = ("/profile/", "/latest/", "/company/", "/companies/", "/quote/", "/videos/",
                      "/news/videos/", "/video/", "/player/", "/press-release", "/sponsored/")
# 경로 어디에든 이 구간이 있으면 영상 페이지 (cbc.ca/player/play/video/…, nbcnews.com/now/video/…)
DROP_PATH_SEGMENTS = frozenset({"video", "videos", "player"})


@dataclass(frozen=True)
class Edition:
    """Google News 지역판 파라미터"""
    hl: str
    gl: str
    ceid: str


EDITIONS = {
    "ko": Edition(hl="ko", gl="KR", ceid="KR:ko"),
    "en-US": Edition(hl="en-US", gl="US", ceid="US:en"),
    "en-SG": Edition(hl="en-SG", gl="SG", ceid="SG:en"),   # 싱가포르 언론사는 en-US판으로 찾으면 더 오래된 기사가 섞인다
    "en-GB": Edition(hl="en-GB", gl="GB", ceid="GB:en"),
    "en-IN": Edition(hl="en-IN", gl="IN", ceid="IN:en"),
}
DEFAULT_EDITION = {Region.KR: "ko", Region.US: "en-US", Region.SG: "en-SG", Region.GLOBAL: "en-US"}

# 검색어 (주제 묶음 × 국가). 실제 쿼리는 `{검색어} (site:a OR site:b ...) when:Nd`
# - intitle:을 붙인다. 없으면 사이드바·관련 기사 텍스트까지 매칭돼 결과가 거의 전부 노이즈다
# - 따옴표("EPC")는 결과를 바꾸지 않는다. 'EPC/플랜트/엔지니어링'은 실측 기간에 추가 적중이 없었지만 주제 범위라 둔다
# - 'AEC'는 국내 기사에서 반도체 케이블·X-ray 노출 제어라 국내 검색어에서 뺐다
# - 판정은 Bedrock이 다시 하므로 검색어는 넓게 잡는다
_EN_DRAWING = '(intitle:BIM OR intitle:blueprints OR intitle:"construction drawings" OR intitle:"engineering drawings")'
_EN_RAG = '"retrieval-augmented" (construction OR engineering OR BIM OR EPC)'
SEARCH_TERMS: dict[str, dict[Region, str]] = {
    "drawing": {
        Region.KR: "(intitle:도면 OR intitle:BIM OR intitle:디지털트윈 OR intitle:CAD)",
        Region.US: _EN_DRAWING,
        Region.SG: _EN_DRAWING,
        Region.GLOBAL: _EN_DRAWING,
    },
    "aec_ai": {
        Region.KR: "intitle:AI (intitle:건설 OR intitle:건축 OR intitle:플랜트 OR intitle:EPC OR intitle:엔지니어링)",
        Region.US: "intitle:AI (intitle:construction OR intitle:contractors OR intitle:architects OR intitle:AEC OR intitle:EPC)",
        Region.SG: 'intitle:AI (intitle:construction OR intitle:BIM OR intitle:"built environment")',
        Region.GLOBAL: "intitle:AI (intitle:construction OR intitle:contractors OR intitle:architects OR intitle:AEC OR intitle:EPC)",
    },
    "rag": {
        Region.KR: "RAG (건설 OR 플랜트 OR 엔지니어링 OR 도면 OR BIM)",
        Region.US: _EN_RAG,
        Region.SG: _EN_RAG,
        Region.GLOBAL: _EN_RAG,
    },
}
SEARCH_KIND_THEME = {"drawing": Theme.DRAWING_BIM, "aec_ai": Theme.AEC_AI, "rag": Theme.AEC_RAG}

# 점검 쿼리: 결과가 0건이면 Google News 응답 이상으로 보고 경고한다 (주제 쿼리는 실제로 0건인 날이 많아
# 0건 경고를 하지 않는다). 한 쿼리의 언론사는 지역판이 같아야 해서 글로벌은 en-GB·en-IN을 따로 본다.
HEALTH_QUERIES: tuple[tuple[Region, str, tuple[str, ...]], ...] = (
    (Region.KR, "intitle:AI", ("chosun", "donga", "mk")),
    (Region.US, "intitle:AI", ("reuters", "cnbc", "bloomberg")),
    (Region.SG, "intitle:AI", ("straitstimes", "cna")),
    (Region.GLOBAL, "intitle:AI", ("bbc", "guardian")),
    (Region.GLOBAL, "intitle:AI", ("ndtv", "hindustantimes")),
)

# Google News는 q가 200자를 넘으면 뒤를 조용히 잘라 when:이 빠진다 (2026-11-12 실측: 200자 정상, 201자부터 무시).
# 여유를 두고 190자로 묶는다.
MAX_QUERY_CHARS = 190


# --- 경쟁사 (🏁 경쟁사 동향 섹션) ---
# 2026-11-13 실측(조사 결과는 CLAUDE.md 'aec-news' 절):
# - 경쟁사 소식은 IT·스타트업 매체, 업계지(ENR, AEC Magazine, Construction Dive), 보도자료 배포처(Business Wire,
#   GlobeNewswire, Yahoo Finance 재게시)에 주로 실린다. 국내 주요 10개사 호스트는 경쟁사 기사의 15%뿐이라
#   주요 언론사 목록으로 제한하지 않는다 (site: 없음).
# - 국내 사명은 이름 하나만 넣는다. OR로 묶으면 Google이 본문 언급 기사부터 버린다 (포비콘 180일 9건 → 3건).
#   포비콘·클라이원트는 거의 본문 언급으로만 보도된다 → 제목에 이름이 없으면 원문 본문을 받아 확인한다.
# - 해외 이름은 동명이인이 심하다: Datagrid(뉴질랜드 데이터센터 개발사가 87%), VIKTOR(오르반·요케레스 등 인물,
#   Slack용 AI 동료 'Viktor'), Provision(일반 명사), Nomic(Nomic Bio, Nomic 체인, 'economic' 퍼지 매칭),
#   Beam AI(Reflection의 Beam, Google Beam), Attentive(SMS 마케팅사), PrimePoint(미국 급여·HR사).
#   → 업종 단서·제외어를 붙이고, Bedrock이 회사 소개(what)와 대조해 다시 확인한다.

@dataclass(frozen=True)
class Competitor:
    key: str
    group: str                         # COMPETITOR_GROUPS 키
    name: str                          # Slack 표시 이름
    what: str                          # 회사 소개 (Bedrock 확인용 — 업종으로 동명이인을 가린다)
    names: tuple[str, ...]             # 기사에 나오는 이름 표기 (Bedrock 프롬프트, 본문 확인용)
    tag_aliases: tuple[str, ...] = ()  # 주제 검색 기사 제목에서 찾을 '고유한' 이름 (정규식, 대소문자 무시).
                                       # 일반어·인명과 겹치는 이름(Provision, Viktor, Datagrid ...)은 넣지 않는다


COMPETITOR_GROUPS = {
    "construction_kr": "건설 스타트업 국내",
    "construction_global": "건설 스타트업 글로벌",
    "rag_kr": "RAG 국내",
}

COMPETITORS: tuple[Competitor, ...] = (
    # 건설 스타트업 국내
    Competitor("fobecon", "construction_kr", "포비콘",
               "건설 도면을 이해하는 AI로 도면 검토·물량 산출(적산)·공사비 견적을 자동화하는 건설 AI 스타트업 "
               "(판교, 대표 송중석, 제품 '오토적산'). 협력사·수상 후보로 본문에만 언급되는 경우가 많다",
               ("포비콘", "FOBECON"), (r"포비콘", r"FOBECON")),
    Competitor("infocz", "construction_kr", "인포시즈",
               "온톨로지·지식그래프 기반 산업 AI 기업 (대표 탁정수). 플랜트 P&ID 등 산업 도면 인식(DELTAflow), "
               "산업 특화 온톨로지(AXIOM), 한전 전력망 AI, 서울대와 산업 도면 AI 공동연구. 인포시스(Infosys)와 다른 회사",
               ("인포시즈", "INFOCZ"), (r"인포시즈", r"INFOCZ")),
    Competitor("planby", "construction_kr", "플랜바이테크놀로지스",
               "설계사·엔지니어링사·건설사용 건축 AI (도면→BIM(IFC) 변환, RFI 답변, 건축 법령 검색, 설계 AI 'Plana', "
               "AI 렌더링)를 고객사 보안 환경에 구축하는 회사",
               ("플랜바이테크놀로지스", "플랜바이", "Planby"), (r"플랜바이", r"Planby")),
    Competitor("doaz", "construction_kr", "두아즈",
               "도면·시추보고서·계산서·시방서 같은 엔지니어링 문서를 AI가 읽고 기준·다른 도서와 맞는지 전수 검증하는 건설·플랜트 "
               "엔지니어링 AI 기업 doAZ(doaz.ai, 대표 김영태). 포스코이앤씨 지반 GeoAI, 대우건설 심의의견-도면 매칭, 롯데건설 "
               "시방서 QA, 두산에너빌리티 P&ID 분석 사례. 콘퍼런스 발표자·협약 참여사로 본문에만 나오는 경우가 많다. "
               "오보이스트 올리비에 두아즈(Olivier Doise) 같은 인명과 다른 회사",
               ("두아즈", "doAZ"), (r"(?<!올리비에 )두아즈", r"doAZ")),
    # 건설 스타트업 글로벌
    Competitor("datagrid", "construction_global", "Datagrid (Procore)",
               "건설·엔지니어링용 AI 에이전트 플랫폼(datagrid.com). 도면·시방서를 해석해 제출물·RFI·입찰 업무 자동화. "
               "2026-01 Procore가 인수해 Procore AI 에이전트로 통합. 뉴질랜드 데이터센터 개발사 Datagrid와 다른 회사",
               ("Datagrid", "Procore AI agents", "Thiago da Costa")),
    Competitor("beamai", "construction_global", "Beam AI (Attentive.ai)",
               "Attentive.ai의 AI 물량산출(takeoff)·견적 소프트웨어 'Beam AI' (ibeam.ai, BIM CoPilot, Takeoff Monkey 인수, "
               "CEO Shiva Dhawan). SMS 마케팅사 Attentive, Reflection의 'Beam' 코딩 모델, Google Beam, beam.ai와 다른 회사",
               ("Beam AI", "Attentive.ai", "Shiva Dhawan"), (r"Attentive\.ai",)),
    Competitor("trunktools", "construction_global", "Trunk Tools",
               "건설 전용 AI 플랫폼. 'Cortex'가 현장 문서·도면 관계를 지식그래프로 만들고 제출물·RFI·도면 검토를 자동화 "
               "(미국, CEO Sarah Buchner)",
               ("Trunk Tools", "TrunkTools"), (r"Trunk ?Tools",)),
    Competitor("primepoint", "construction_global", "Primepoint",
               "도면 기반 건설 인텔리전스 플랫폼(primepoint.ai). AI가 시공 도면을 읽고 시방서·공정표와 연결해 시공성 검토·"
               "제출물 검토·RFI·도면 개정 비교 (Autodesk Forma 연동, 창업자 Lubomir Bourdev). 미국 급여·HR사 PrimePoint와 다른 회사",
               ("Primepoint", "Lubomir Bourdev")),   # 'PrimePoint'(급여·HR사)와 겹쳐 제목 태깅·규칙 판정에 쓰지 않는다
    Competitor("wyreai", "construction_global", "Wyre AI",
               "프리컨스트럭션 리스크 관리 AI(wyreai.io). 도면·시방서 세트로 공종별 스코프 패키지를 만들고 누락·충돌·RFI 대상을 "
               "입찰 전에 찾음 (창업자 Sunil Dorairajan). MSP용 'WYRE Technology', 암호화폐 결제사 Wyre와 다른 회사",
               ("Wyre AI", "WyreAI", "Sunil Dorairajan"), (r"Wyre ?AI",)),
    Competitor("egnyte", "construction_global", "Egnyte",
               "기업용 콘텐츠 클라우드(파일 공유·협업·거버넌스)와 AI 검색·에이전트(Context Studio). AEC가 핵심 산업 분야. "
               "건설 외 일반 기업용 소식도 그 회사 소식이다",
               ("Egnyte",), (r"Egnyte",)),
    Competitor("nomic", "construction_global", "Nomic",
               "AEC 회사용 도메인 특화 AI 에이전트 플랫폼(nomic.ai) — 도면 법규 검토, 제출물 검토, 도면 QA/QC, 전사 문서 검색. "
               "GPT4All·Nomic Embed를 만든 Nomic AI(CEO Andriy Mulyar)가 AEC로 전환, Arcadis·Aurecon 투자. "
               "단백질체학 Nomic Bio, Nomic 블록체인과 다른 회사",
               ("Nomic", "Nomic AI", "Andriy Mulyar"), (r"Nomic AI", r"Nomic\.ai")),
    Competitor("viktor", "construction_global", "VIKTOR",
               "네덜란드의 엔지니어링 자동화 플랫폼(viktor.ai) — 엔지니어가 AI로 구조·지반·BIM·MEP 계산·설계 검토 앱을 만들고 "
               "ETABS·Revit·Civil 3D 등과 연동. Slack/Teams용 AI 동료 'Viktor'(Accel 투자) 및 Viktor라는 인물과 다른 회사",
               ("VIKTOR", "VIKTOR.AI")),
    Competitor("provision", "construction_global", "Provision",
               "종합건설사·하도급사용 프리컨스트럭션 AI(provision.com, 캐나다 Provision Software). 도면·시방서·계약서로 "
               "공종별 스코프 작성·누락 탐지, 출처 인용 질의응답, 계약 위험 조항 검토, RFI 탐지 (CEO Luigi La Corte). "
               "'provision'(조항·충당금·공급)이라는 일반 단어 기사와 구별",
               ("Provision", "Provision Software", "Scope Agent")),
    Competitor("documentcrunch", "construction_global", "Document Crunch (Trimble)",
               "건설 계약서·시방서·도면을 건설 특화 AI(CrunchAI)로 검토해 위험 조항·스코프 누락·상충 요구사항을 출처와 함께 찾는 "
               "건설 리스크 관리 플랫폼(documentcrunch.com, 공동창업자 Josh Levy). 2026-04 Trimble이 인수해 'Document Crunch, "
               "a Trimble company'로 운영 — Trimble 이름의 보도자료가 본문에서만 언급하기도 한다. 'crunch'가 든 다른 기사(TechCrunch "
               "등), Trimble 실적 요약에 이름만 스친 기사와 구별",
               ("Document Crunch", "DocumentCrunch", "CrunchAI"), (r"Document ?Crunch", r"CrunchAI")),
    Competitor("swapp", "construction_global", "SWAPP",
               "건축설계사무소용 AI 실시설계 도면(CD 세트)·BIM 자동화 플랫폼(swapp.ai, 이스라엘, CEO Eitan Tsarfati, CTO Adi Shavit). "
               "Revit·ArchiCAD 안에서 AI 에이전트가 사무소 표준에 맞춰 치수·태그·뷰·시트를 작성하고 QA/QC (고객 Stantec·HGA 등). "
               "성씨 Swapp(부고, Craig Swapp 법률사무소), 인도 배터리 교체사 Swapp Design, Swapp for Mac, 'swap'과 다른 회사",
               ("SWAPP", "SWAPP.AI", "Eitan Tsarfati", "Adi Shavit"), (r"SWAPP\.AI",)),
    # RAG 국내
    Competitor("bhsn", "rag_kr", "BHSN",
               "자체 Legal-LLM 기반 기업 법무 AI '앨리비(allibee)'를 만드는 리걸테크 기업 (계약 검토·이행관리, 법령·판례 검색, "
               "리걸 OCR). 미국 학교 약칭 BHSN과 다른 회사",
               ("BHSN", "비에이치에스엔", "앨리비", "allibee"), (r"BHSN", r"비에이치에스엔", r"앨리비", r"allibee")),
    Competitor("koreadeep", "rag_kr", "한국딥러닝",
               "문서를 읽고 근거로 답하는 기업용 AI(DEEP OCR·DEEP Parser·DEEP Agent)를 고객사 내부망에 구축 (대표 김지현). "
               "금융·보험·공공·제조(도면 검증 포함) 서류를 VLM으로 판독·자동 처리",
               ("한국딥러닝", "Korea Deep Learning"), (r"한국딥러닝", r"Korea Deep Learning")),
    Competitor("allganize", "rag_kr", "올거나이즈",
               "에이전틱 RAG·자체 LLM 기반 엔터프라이즈 AI 플랫폼 'Alli'를 제공하는 기업용 생성형 AI 기업 (대표 이창수, "
               "2026-11 Allganize Holdings로 도쿄증권거래소 그로스 상장)",
               ("올거나이즈", "Allganize"), (r"올거나이즈", r"Allganize")),
    Competitor("cliwant", "rag_kr", "클라이원트",
               "공공조달 입찰 전 과정을 AI 에이전트로 지원하는 B2G 수주 플랫폼 (대표 조준호, 공고 매칭·RFP 분석·제안서 초안). "
               "언론에는 '클라이원트에 의뢰해 분석한 결과'처럼 데이터 출처로 본문에만 나오는 경우가 많다",
               ("클라이원트", "Cliwant"), (r"클라이원트", r"Cliwant")),
    Competitor("graphai", "rag_kr", "그래파이",
               "KAIST 김민수 교수가 창업한 엔터프라이즈 AI 데이터 인프라 기업 그래파이(GraphAI, graphai.io). 그래프·벡터·관계형 DB를 "
               "합친 RAG용 DB '아카식DB(AkasicDB)', 문서 데이터화 AkasicIN, 그래프 RAG·온톨로지 (2026-08 시리즈A 170억 원). "
               "흑연 '그래파이트'·그래핀, 치과 3D프린팅 소재사 그래피(Graphy), 미국 환자안전 AI 'Graph AI'와 다른 회사",
               ("그래파이", "GraphAI", "아카식DB", "AkasicDB"), (r"그래파이(?!트)", r"아카식 ?DB", r"Akasic ?DB")),
    Competitor("wisenut", "rag_kr", "와이즈넛",
               "검색엔진·챗봇에서 출발한 코스닥 상장(096250) AI 에이전트 기업 와이즈넛(Wisenut, 대표 강용성). RAG 솔루션 WISE iRAG, "
               "자체 LLM, 노코드 에이전트 도구 WISE Agent Labs, 공공·금융·커머스 AI 구축. 실적·수주·자사주 같은 회사 발표는 회사 "
               "소식이지만, 주가 등락만 다룬 시황·테마주(특징주) 기사와 다른 회사 IPO 기사의 비교기업 언급은 0.3 이하",
               ("와이즈넛", "Wisenut"), (r"와이즈넛", r"Wisenut")),
    Competitor("sionic", "rag_kr", "사이오닉에이아이 (Sionic AI)",
               "기업용 생성형 AI 플랫폼 '스톰(STORM)'(노코드 AI 에이전트 빌더, RAG, 문서 파싱 STORM Parse, 문서인식 PepperOCR-VL)을 "
               "온프레미스·폐쇄망·SaaS로 공급하는 사이오닉에이아이(sionic.ai, 대표 고석현, 태국·싱가포르 진출). 미국 바이오닉 의수 기업 "
               "사이오닉(PSYONIC), 미국 결제사 Sionic, 스타크래프트 '사이오닉 스톰'과 다르다",
               ("사이오닉에이아이", "사이오닉AI", "사이오닉", "Sionic AI"),
               (r"사이오닉 ?에이아이", r"사이오닉 ?AI", r"Sionic ?AI", r"Pepper ?OCR", r"페퍼 ?OCR")),
)

# 경쟁사 검색어: (검색어, 지역판, 후보 회사 키). 실제 쿼리는 `{검색어} when:Nd` (≤190자, 테스트가 검사)
# - 고유한 해외 이름은 한 쿼리에 묶어도 손실이 없었다 (Q1). 일반어 이름은 intitle: + 업종 단서로 따로 둔다
COMPETITOR_QUERIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("포비콘", "ko", ("fobecon",)),
    ("인포시즈", "ko", ("infocz",)),
    ("플랜바이", "ko", ("planby",)),
    ("플랜바이테크놀로지스", "ko", ("planby",)),
    ("BHSN", "ko", ("bhsn",)),
    ("한국딥러닝", "ko", ("koreadeep",)),
    ("올거나이즈", "ko", ("allganize",)),
    ("클라이원트", "ko", ("cliwant",)),
    ("두아즈 -올리비에", "ko", ("doaz",)),          # 오보이스트 올리비에 두아즈 기사 제외
    ("그래파이", "ko", ("graphai",)),
    ("와이즈넛", "ko", ("wisenut",)),
    ("사이오닉", "ko", ("sionic",)),                # 맨이름이 사이오닉에이아이·사이오닉AI 표기를 모두 포함 (+ 본문 언급)
    # 고유한 해외 이름 묶음. Google은 OR 쿼리에 관련도순 몇 건만 준다 — 물량이 많은 Egnyte는 따로 뺐다 (2026-11-13)
    ('("Trunk Tools" OR TrunkTools OR "Wyre AI" OR WyreAI)', "en-US", ("trunktools", "wyreai")),
    ("Egnyte", "en-US", ("egnyte",)),
    ('(Primepoint OR "Attentive.ai" OR "Beam AI") (construction OR drawings OR contractors OR takeoff OR estimating '
     'OR BIM OR Autodesk)', "en-US", ("primepoint", "beamai")),
    ("Datagrid Procore", "en-US", ("datagrid",)),
    ("intitle:Procore (intitle:AI OR intitle:agents OR intitle:agentic OR intitle:Datagrid)", "en-US", ("datagrid",)),
    ("intitle:Nomic (AEC OR construction OR architecture OR architects OR engineers OR Arcadis OR Aurecon OR drawings "
     "OR BIM)", "en-US", ("nomic",)),
    ('intitle:VIKTOR (engineering OR engineers OR AEC OR BIM OR Autodesk OR structural OR geotechnical OR "app builder") '
     "-Orbán -Orban -Gyökeres -Gyokeres", "en-US", ("viktor",)),
    # 인수 뒤 Trimble 이름의 보도자료가 본문에서만 언급 — intitle:을 쓰지 않는다. OR 묶음에 넣으면 진짜 소식이 빠졌다
    ('"Document Crunch"', "en-US", ("documentcrunch",)),
    ('"SWAPP" (architects OR architecture OR architectural OR Revit OR ArchiCAD OR BIM OR AEC OR "construction documents") '
     '-obituary -"Swapp Design"', "en-US", ("swapp",)),
    ('intitle:Provision (preconstruction OR "pre-construction" OR estimators OR "scope gaps" OR "Scope Agent" '
     'OR "general contractors" OR "Luigi La Corte")', "en-US", ("provision",)),
)

# 경쟁사 검색 결과에서 버릴 출처 (블로그·재게시·자동 생성 페이지·스팸). 호스트가 이것이거나 이것으로 끝나면 제외
COMPETITOR_BLOCK_HOSTS = (
    "brunch.co.kr", "blog.naver.com", "m.blog.naver.com", "tistory.com", "v.daum.net",
    "calgaryroughnecks.com", "dealroom.co", "getlatka.com", "maxpreps.com", "cloudwards.net", "g2.com",
    # 자동 생성 주식 투자분석·실적 요약 (와이즈넛·Trimble 결과의 잡음)
    "judal.co.kr", "tradingview.com", "stockstory.org",
)
