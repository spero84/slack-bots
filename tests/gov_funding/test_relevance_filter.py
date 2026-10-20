"""1차 필터 (지역/키워드/결과·예고/마감일) 테스트

실데이터 제목은 2026-10 진단(기업마당/K-Startup/JOINTIPS/NIA 목록, S3 Vectors 저장 제목, Slack 게시 이력)에서 가져왔다.
"""
import logging
from datetime import datetime

import pytest

from src.gov_funding.analyzers import relevance_filter
from src.gov_funding.analyzers.relevance_filter import (
    REASON_EXCLUDE,
    REASON_NO_KEYWORD,
    REASON_NON_ACTIONABLE,
    REASON_REGION,
    REASON_SOFT_EXCLUDE,
    _contains_exclude_keyword,
    _contains_relevance_keyword,
    _is_allowed_region,
    calculate_keyword_score,
    classify_keywords,
    classify_region,
    deadline_filter,
    keyword_filter,
)
from src.gov_funding.storage.models import Announcement, Source


def make(title, source=Source.KSTARTUP, category=None, department=None, **kwargs):
    return Announcement(
        id=kwargs.pop("id", "1"),
        source=source,
        title=title,
        category=category,
        department=department,
        url="https://example.com",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 지역 판정
# ---------------------------------------------------------------------------

# 진단 프로토타입 검증 케이스 29건 (t3d_validate_proposed.py) — (제목, 소관부처, 허용 여부)
PROTOTYPE_REGION_CASES = [
    ("[공고] 2026년 AI 바우처 지원사업 수요기업 모집", None, True),
    ("[재공고] 2026년 SW 고성장클럽 모집", None, True),
    ("[모집] 2026 AI 스타트업 글로벌 진출 지원", None, True),
    ("[연장] 2026년 데이터바우처 공급기업 모집", None, True),
    ("[입찰공고] 클라우드 전환 컨설팅 용역", None, True),
    ("[한국투자액셀러레이터] 바른동행 4차 모집", None, True),
    ("[코리아스타트업포럼] IR데이 <The PITCH: AI Edition> 참가기업 모집", None, True),
    ("[경기] 광주시 2026년 AI 창업지원", "경기도", True),
    ("경기 광주 스타트업 지원센터 입주기업 모집", None, True),
    ("[서울] 2026 AI 스타트업 지원 (부산·대구 기업 참여 가능)", "서울특별시", True),
    ("[경기] 성남시 전남광주-성남cnd 연계 바이오 인프라 프로모션 참여기업 모집 공고", "경기도", True),
    ("2026년 대구국제기계산업대전 참가지원 추가모집 공고", "경기도", True),
    ("2026년 웰컴 투 팁스 5차 참가기업 모집 (호남권)", None, False),
    ("2026 영남권 AI 스타트업 데모데이", None, False),
    ("동남권 디지털 혁신 지원사업", None, False),
    ("부울경 AI 창업기업 모집", None, False),
    ("비수도권 SW 기업 지원사업", None, False),
    ("전라권 데이터 기업 지원", None, False),
    ("충청권 엔젤투자 피칭룸", None, False),
    ("2026 대경권 엔젤투자 포럼", None, False),
    ("2026년 여성CEO 비즈니스 아카데미 경상권역 시즌 2", None, False),
    ("KETI 파트너쉽 대덕특구 연구소기업", None, False),
    ("2026년 AI 사업화 지원 공고", "충청남도", False),
    ("2026년 AI 사업화 지원 공고", "경상북도", False),
    ("2026년 AI 사업화 지원 공고", "전남광주통합특별시", False),
    ("광주광역시 AI 창업 지원", None, False),
    ("[인천지식재산센터] IP디딤돌 아이디어 권리화 지원사업 모집 공고", None, False),
    ("2026년 AI 바우처 지원사업", "과학기술정보통신부", True),
    ("[충북] 충주시 2026년 제조 AI 현장 적용 지원", "중소벤처기업부", False),
]


@pytest.mark.parametrize("title, department, allowed", PROTOTYPE_REGION_CASES)
def test_region_prototype_cases(title, department, allowed):
    assert (classify_region(title, None, department).verdict != "exclude") is allowed


# 실제 기업마당(소관부처 포함)/K-Startup/JOINTIPS 제목
REAL_REGION_CASES = [
    # 기관/태그 대괄호는 지역이 아님 → 전국 대상
    ("[한국투자액셀러레이터] 바른동행 4차 모집｜선발 시 최대 5억원 투자", None, None, True),
    ("[코리아스타트업포럼] IR데이 <The PITCH: AI Edition> 참가기업 모집", None, None, True),
    ("[무료] 창업자를 위한 금융지원 컨설팅", None, None, True),
    ("[신한스퀘어브릿지] 동남아 시장 진입과 사업 확장 실행 전략 - 9월 브릿지클럽 참가자 모집", None, None, True),
    ("[중앙사회서비스원] 2026년 제3회 사회서비스 투자교류회", "행사ㆍ네트워크", None, True),
    ("[해수부] 2026년 해양수산 통합데모데이 참가자 모집", None, None, True),
    ("[GBSA] 2026 판교스타트업 투자교류회 제 3차 투자교류회", None, None, True),
    ("[수도권] 2026년 AI 창업기업 지원사업 공고", None, "중소벤처기업부", True),
    ("[성남산업진흥원] 2026년 투자활성화 지원 사업 참가기업 모집 공고", "공공", None, True),
    ("2026 경기 고양 MICE 연계 창업리그 참가자 모집 공고", None, None, True),
    ("2026년 서울창업센터 관악 청년창업 역량강화(서남권 AI Full-Stack) 프로그램 참가자 모집", None, None, True),
    ("2026년 영등포구 소상공인 디지털 전환교육 교육생 모집", None, None, True),
    # 기업마당 소관부처 (구조화 정보)
    ("[경기] 안산시 2026년 일자리 창출 장려를 위한 고용우수기업 인증제 시행 공고", "기타", "경기도", True),
    ("2026년 7차 해외 상표 무단선점 심층조사 지원사업 모집 공고", "수출", "지식재산처", True),
    ("[서울] 광진구 2026년 우리동네 소상공인 경영주치의(시설개선) 참여업체 모집 공고", "경영", "서울특별시", True),
    ("[충남] 홍성군 2026년 (예비)사회적기업 시설장비 지원사업 공고", "인력", "충청남도", False),
    ("2026년 하반기 아산시 단체관광객 유치 인센티브 지원계획 공고", "경영", "충청남도", False),
    ("[대구] 2026년 지역특화콘텐츠개발지원(대구게임테크허브) 게임 스케일업 패키지 지원사업 참가기업 모집 공고",
     "기술", "대구광역시", False),
    ("[전북] 군산시 2026년 창업플랫폼 예비창업자 모집 공고", "창업", "전북특별자치도", False),
    ("[강원] 글로벌 점프업 패키지 지원 사업 참가기업 모집 공고", "수출", "강원특별자치도", False),
    ("[세종] 2026년 2차 지역형 예비사회적기업 지정계획 공고", "인력", "세종특별자치시", False),
    ("[전남광주] 2026년 창업도시 프로젝트 기술개발 컨설팅 참가자 모집 공고", "기술", "전남광주통합특별시", False),
    # 소관부처 없이 제목만 (K-Startup/JOINTIPS)
    ("[경북] 2026년 경북세일페스타 수도권 팝업스토어 참가기업 모집 공고", None, None, False),
    ("[대전ㆍ세종] 2026년 코리아 그랜드 페스티벌 지역 소상공인 라이브커머스 참여기업 추가 모집 공고", None, None, False),
    ("[세종] 2026 중부권 지역혁신클러스터육성사업(비R&D) 기업지원 수혜기업 모집 공고", None, None, False),
    ("[강원] 2026년 지역 주도형 AI 대전환 사업 강원 지역주도 AI 대전환 수요기업 모집 공고", None, None, False),
    ("2026년 웰컴 투 팁스 7차 참가기업 모집 (제주권)", None, None, False),
    ("「2026년 웰컴 투 팁스」 2차(대경권) 참가기업 모집", None, None, False),
    ("2026-9회 충청권 엔젤투자 피칭룸 프로그램 참가기업 모집", "민간", None, False),
    ("2026 제4회 동남권 엔젤투자피칭룸 참여기업 모집 공고", "민간", None, False),
    ("2026년 스타트업 법률지원사업 울산광역시 법률상담회 참여 기업 모집 안내", "멘토링ㆍ컨설팅ㆍ교육", None, False),
    ("천안시 거점형 스마트도시 조성사업 「2026년 천안시 어반테크기업 클라우드 바우처 지원사업」 참여기업 모집공고",
     "공공", None, False),
    ("『2026년 익산형 청년창업 액셀러레이팅 지원사업(TIPS 연계형)』 참여자 모집 공고", "지자체", None, False),
    ("2026 대덕특구 딥테크 혁신성장 플랫폼(전략기술 발굴 및 매칭)", "공공", None, False),
    ("「2026 경상북도 예비유니콘 성장지원사업」참여기업 모집 공고", "공공", None, False),
    ("2026년 충청권 ICT이노베이션스퀘어 개발 역량 강화 멘토링", "민간", None, False),
]


@pytest.mark.parametrize("title, category, department, allowed", REAL_REGION_CASES)
def test_region_real_titles(title, category, department, allowed):
    assert (classify_region(title, category, department).verdict != "exclude") is allowed


# 지역명 오탐 (부분 문자열 함정) — 지역 공고가 아니므로 전국 대상
@pytest.mark.parametrize("title", [
    "2026년 AI 대전환 지원사업 수요기업 모집",          # 대전환
    "2026 대한민국 소프트웨어대전 참가기업 모집",         # 전시회 '대전'
    "농업부산물 활용 AI 플랫폼 실증",                   # 부산물
    "창업 전주기 지원 프로그램 참가기업 모집",            # 전주기(全週期)
    "세종대왕 한글 AI 말뭉치 구축",                     # 세종대왕
    "세종학당재단 한국어 AI 교육 콘텐츠 개발",            # 세종학당
    "아산나눔재단 마루180 입주기업 모집",                # 아산나눔재단 (서울)
    "서울아산병원 의료AI 스타트업 협업 프로그램",          # 서울아산병원
    "2026 핀테크 여수신 데이터 활용 공모전",              # 여수신
])
def test_region_substring_traps_are_not_regions(title):
    assert classify_region(title).verdict != "exclude"


@pytest.mark.parametrize("title, allowed", [
    ("세종시 AI 창업기업 지원", False),
    ("세종특별자치시 데이터 기업 모집", False),
    ("2026 대전 팁스타운 제2차 팁스 링크 일본 파트너 초청 상담회", False),
    ("광주 AI 창업 지원", False),                         # 단독 광주 (경기 표시 없음)
    ("경기도 광주시 스타트업 지원", True),
    ("광주시(경기) 창업보육센터 입주기업 모집", True),      # 허용 지역 표시가 있으면 단독 광주는 무시
    ("서울 동남권 창업지원센터 입주기업 모집", True),       # 서울 생활권 '동남권'
    ("（호남권） AI 스타트업 모집", False),                # 전각 괄호 (NFKC)
    ("[재공고][부산] AI 바우처 수요기업 모집", False),       # 둘째 태그가 지역
    ("[공고][서울] AI 바우처 (부산 기업 참여 가능)", True),  # 둘째 태그 [서울]이 결정
    ("[인천·경기] AI 창업기업 모집", True),                # 태그에 허용 지역이 있으면 허용
    ("[비수도권] SW 기업 지원", False),
    ("[광주] 2026 AI 창업 지원", False),                   # 태그 안의 단독 광주
])
def test_region_rules(title, allowed):
    assert (classify_region(title).verdict != "exclude") is allowed


def test_region_procurement_category_ignores_title_region():
    """조달/입찰 공고의 제목 지역명은 수행 장소라 제외하지 않는다 (NIA 조달입찰공고)"""
    verdict = classify_region(
        "경상북도 지방소멸 위기 극복을 위한 주민케어 AI 서비스", "조달입찰공고", "한국지능정보사회진흥원"
    )
    assert verdict.verdict == "nationwide"
    assert classify_region("2026년 충청남도교육청 학교 무선통신장비 도입 및 설치", "사전규격공개").verdict == "nationwide"
    # 조달이 아닌 공고는 그대로 제외
    assert classify_region("경상북도 지방소멸 위기 극복을 위한 주민케어 AI 서비스", "사업공고").verdict == "exclude"


def test_region_verdict_kinds():
    assert classify_region("[서울] AI 창업 지원").verdict == "allow"
    assert classify_region("2026년 AI 바우처 지원사업").verdict == "nationwide"
    assert classify_region("부산 AI 창업 지원").verdict == "exclude"
    assert classify_region("부산 AI 창업 지원").reason == "부산"


def test_is_allowed_region_bool_contract():
    assert _is_allowed_region(make("[코리아스타트업포럼] IR데이 참가기업 모집")) is True
    assert _is_allowed_region(make("2026년 AI 사업화 지원 공고", source=Source.BIZINFO, department="충청남도")) is False
    assert _is_allowed_region(make("2026년 AI 바우처", category="부산")) is False


# ---------------------------------------------------------------------------
# 관련/제외 키워드
# ---------------------------------------------------------------------------

# 진단 검증 목록 (t4d_validate_kw.py)
SHOULD_PASS = [
    "AI 제조 혁신 바우처 수요기업 모집",
    "스마트제조 AI 솔루션 공급기업 모집",
    "중소기업 AX 전환 지원사업",
    "에너지 AI 데이터 플랫폼 실증",
    "바이오 데이터 활용 AI 신약 플랫폼",
    "AI 물류 로봇 실증 사업",
    "업종 제약 없는 AI 스타트업 투자유치 지원",
    "제조 특화 온디바이스 AI 기반 자율제조 실증 기반 구축사업",
    "정보보안 기업 해외진출 지원",
    "2026년도 융합보안핵심인재양성 사업 신규과제 공고",
    "하노이 IT지원센터‘26년 하반기 추가 입주기업 모집 공고",
    "2026년 산업전문인력 AI역량강화 지원사업",
    "AI·ICT 연구지원 서비스",
    "2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고",
]
SHOULD_DROP = [
    "UNIT 공사 입찰",
    "TRAINING 센터 운영 용역",
    "BLOCKCHAIN 세미나",
    "MAIN STAGE 부스 운영",
    "SWISS 박람회 참가",
    "청년 농업인 창업 교육",
    "2026년 하반기 아산시 단체관광객 유치 인센티브 지원계획 공고",
    "에너지 효율 설비 교체 지원",
    "바이오 의약품 GMP 컨설팅",
    "물류센터 운영 인력 모집",
]


@pytest.mark.parametrize("title", SHOULD_PASS)
def test_keyword_should_pass(title):
    verdict = classify_keywords(make(title))
    assert verdict.passed, verdict


@pytest.mark.parametrize("title", SHOULD_DROP)
def test_keyword_should_drop(title):
    verdict = classify_keywords(make(title))
    assert not verdict.passed, verdict


# 실제 기업마당/IITP/NIPA 제목: 기존 필터가 떨어뜨렸지만 새 키워드/IT 예외로 Bedrock까지 가야 하는 공고
@pytest.mark.parametrize("title, category", [
    ("6G 서비스 시나리오 공모전 - 6G 시연 기획 Track 공고", "경영"),
    ("[경기] 2026년 4차 사물인터넷(IoT) 설치 지원사업 모집 공고", "경영"),
    ("2027년 나노반도체 연구성과 실용화 지원사업 (혁신기업 양산시제품 개발지원) 신규과제 공고", "기술"),
    ("2026년 KoVAC XR 쇼룸 입주기업 2차 모집", "KoVAC XR쇼룸 상설전"),
    ("2026년 산업혁신기반구축사업 제조 특화 온디바이스 AI 기반 자율제조 실증 기반 구축사업 기업지원(기술지도) 프로그램 수혜기업 모집 공고", "기술"),
    ("[경남] 2026년 제조AX 산학혁신파크 사업 AX 기초컨설팅 지원사업 참여기업 모집 공고", "기술"),
    ("2026년도 융합보안핵심인재양성 사업 신규과제 공고", None),
    ("지방정부 정보보안 업무 지원체계 혁신방안 연구", "사전규격공개"),
])
def test_keyword_recovered_real_titles(title, category):
    """지역은 따로 검증하므로 키워드 판정만 본다"""
    text = f"{title} {category or ''}"
    assert _contains_relevance_keyword(text)
    verdict = classify_keywords(make(title, category=category))
    assert verdict.reason in (None, REASON_REGION), verdict


@pytest.mark.parametrize("title, category", [
    ("이데일리 K-BIO DEAL SUMMIT 2026", "포럼"),
    ("2026년 The GS Challenge. FUTURE RETAIL 4 오픈이노베이션 프로그램 개최 안내", "공공"),
])
def test_keyword_ascii_substring_false_positives_removed(title, category):
    verdict = classify_keywords(make(title, category=category))
    assert verdict.reason == REASON_NO_KEYWORD


@pytest.mark.parametrize("text, expected", [
    ("AICT 표준기술 개발 지원사업", True),     # 앞 경계만: AICT의 AI
    ("AIoT융합부품 성능검증", True),
    ("AI특화병원 AX-Ready 시범사업", True),
    ("ICT융합 스타트업", True),
    ("하노이 IT지원센터 입주", True),          # IT 뒤가 한글
    ("IT·SW 융합", True),
    ("SW중심대학", True),
    ("B5G 특화망 실증", True),                 # 숫자로 시작하는 키워드는 앞 숫자만 경계
    ("5G특화망 구축", True),
    ("R&D 과제 공모", True),
    ("Agentic AI Alliance", True),
    ("K-BIO DEAL SUMMIT", False),              # SUMMIT의 IT
    ("FUTURE RETAIL", False),                  # RETAIL의 AI
    ("TRAINING CENTER", False),
    ("BLOCKCHAIN DAY", False),                 # 한글 '블록체인'은 매칭되지만 영문 대문자는 아님
    ("SWISS EXPO", False),
    ("SWITCH 2026", False),
    ("UNIT 공사", False),
    ("ITEM 판매", False),
    ("MAIN STAGE", False),
])
def test_ascii_keyword_boundaries(text, expected):
    assert _contains_relevance_keyword(text) is expected


def test_korean_keyword_substring_and_new_keywords():
    for text in ["사물인터넷 실증", "메타버스 허브", "블록체인 기업성장허브", "핀테크 랩", "로봇 실증", "사이버보안 인력"]:
        assert _contains_relevance_keyword(text), text


@pytest.mark.parametrize("title, reason", [
    ("제약사 오픈이노베이션 참가기업 모집", REASON_EXCLUDE),
    ("조선업 기자재 국산화 지원", REASON_EXCLUDE),
    ("2027년 시군거점 축산물 산지가공 유통시설구축 사업 공고", REASON_EXCLUDE),
    ("[전북] 2027년 시군거점 축산물 산지가공 유통시설구축 사업 공고", REASON_REGION),  # 지역이 먼저
    ("2026년 3차 소공인 클린제조환경조성 사업 모집 공고", REASON_SOFT_EXCLUDE),
    ("2026년 에너지이용합리화자금 융자사업 변경 공고", REASON_SOFT_EXCLUDE),
])
def test_exclude_keywords(title, reason):
    assert classify_keywords(make(title)).reason == reason


@pytest.mark.parametrize("title", [
    "업종 제약 없는 AI 스타트업 투자유치 지원",   # '제약'은 제외 키워드가 아님
    "조선대학교 창업지원단 스타트업 모집",         # '조선'은 제외 키워드가 아님
    "문화체육관광부 콘텐츠 스타트업 창업 지원",     # 문화체육관광부의 '관광'
])
def test_exclude_keyword_substring_traps(title):
    assert classify_keywords(make(title)).passed


def test_exclude_trap_economy_organization_is_not_manufacturing():
    """'사회연대경제조직'의 '제조'는 제외 키워드가 아니다 (관련 키워드도 없어 그 사유로 제외)"""
    verdict = classify_keywords(make("한국전력공사 2026년 사회연대경제조직 지원 사회공헌사업 참여사 모집", category="대회"))
    assert verdict.reason == REASON_NO_KEYWORD


def test_strong_it_signal_overrides_hard_and_soft_excludes():
    hard = classify_keywords(make("스마트팜 농업 데이터 플랫폼 구축"))
    soft = classify_keywords(make("에너지 AI 데이터 플랫폼 실증"))
    assert hard.passed and hard.it_override
    assert soft.passed and soft.it_override


def test_strong_it_signal_is_title_only():
    """분야에만 IT 단어가 있으면 제외 키워드를 무시하지 않는다"""
    verdict = classify_keywords(make("물류센터 운영 인력 모집", category="디지털"))
    assert verdict.reason == REASON_SOFT_EXCLUDE


def test_contains_exclude_keyword_raw():
    assert _contains_exclude_keyword("농업 혁신 지원")
    assert _contains_exclude_keyword("축산업 현대화")
    assert _contains_exclude_keyword("조선업 지원")
    assert _contains_exclude_keyword("제조 혁신")
    assert not _contains_exclude_keyword("AI 스타트업")
    assert not _contains_exclude_keyword("업종 제약 없는 지원")


def test_relevance_keyword_detection():
    assert _contains_relevance_keyword("AI 스타트업 지원")
    assert _contains_relevance_keyword("소프트웨어 개발")
    assert _contains_relevance_keyword("IT 창업")
    assert _contains_relevance_keyword("인공지능 R&D")
    assert not _contains_relevance_keyword("농업 지원사업")


def test_keyword_score():
    score1 = calculate_keyword_score("AI 스타트업 창업 IT SW 개발")
    score2 = calculate_keyword_score("일반 지원사업")
    assert score1 > score2
    assert 0 <= score1 <= 1
    assert score2 == 0
    assert calculate_keyword_score("SUMMIT RETAIL SWITCH") == 0


# ---------------------------------------------------------------------------
# 결과/예고 등 신청 불가 공고
# ---------------------------------------------------------------------------

# 실제로 Bedrock을 통과해 Slack에 게시됐거나 저장된 결과/예고 공고
NON_ACTIONABLE_REAL = [
    ("2026년 산업전문인력 AI역량강화 지원사업 발표평가 결과 및 지원 대상과제 선정 공고", "산업전문인력 AI역량강화"),
    ("2026년 산업전문인력 AI역량강화 지원사업 서류평가 결과 및 발표평가 대상자 선정 공고", "산업전문인력 AI역량강화"),
    ("2026년 AI+S&T 혁신 기술개발(R&D) 사업 신규과제 선정결과 공고", None),
    ("2026년 차세대 AI+S&T 기반기술 개발(R&D) 사업 신규과제 선정결과 공고", None),
    ("2026년 차세대광기반연산반도체핵심기술개발사업 신규과제 선정결과 공고", None),
    ("2026년 첨단기술을 활용한 사회문제해결 실증확산지원(기술개발 및 실증)신규과제 선정결과 공고", None),
    ("2026년도 미래개척융합과학기술개발사업 신규과제 선정결과 공고", None),
    ("2026년도 민관협력 기반 AI 휴머노이드 원천기술 고도화 신규과제 선정결과 공고문", None),
    ("제13회 범정부 공공데이터 활용 창업경진대회 왕중왕전 결과 발표", "사업공고"),
    ("AI기본법 시행 및 하위법령 공개(1.22.~)", "사업공고"),
    ("이러닝(전자학습)산업 발전 및 이러닝 활용 촉진에 관한 법률 시행령 일부개정령(안) 입법예고", None),
    ("AI 데이터 활용 SW 개발 지원 최종 합격자 발표", None),
    ("2026년 AI 바우처 지원사업 지원 대상자 선정 안내", None),
    ("AI 기반 서비스 실증 과제", "평가결과 공개"),  # 분야로도 판정
]


@pytest.mark.parametrize("title, category", NON_ACTIONABLE_REAL)
def test_non_actionable_excluded_even_with_it_signal(title, category):
    verdict = classify_keywords(make(title, source=Source.MSIT, category=category))
    assert verdict.reason == REASON_NON_ACTIONABLE, verdict


# 마감 표시가 붙은 공고 (목록상 기간이 남아 있어도 이미 접수 마감)
CLOSED_MARKER_TITLES = [
    "[접수마감]「첨단 GPU 활용 지원 사업」사용자 수시 모집 공고",          # 2026-10-20 dry-run NIPA
    "2026년 제 1차 서울 팁스타운 입주기업 모집 공고(마감)",                # 실제 저장 제목
    "[모집마감] AI 바우처 수요기업 모집",
    "[마감] AI 바우처 수요기업 모집",
    "(마감) AI 바우처 수요기업 모집",
    "【마감】 AI 바우처 수요기업 모집",
    "［마감］ AI 창업 지원",                                              # 전각 괄호
    "[ 접수 마감 ] AI 창업 지원",
    "AI 바우처 수요기업 모집 [조기마감]",
    "AI 바우처 수요기업 모집 (조기 마감)",
    "AI 바우처 수요기업 모집 조기 마감 안내",
    "AI 스타트업 모집 - 조기마감",
]

# 마감을 언급하지만 마감 표시가 아닌 제목은 유지
DEADLINE_MENTION_TITLES = [
    "AI 바우처 마감 임박 안내",
    "AI 바우처 접수 마감일 연장 공고",
    "AI 바우처 마감기한 연장",
    "AI 바우처 (마감일 연장)",
    "[마감임박] AI 바우처 모집",
    "AI 창업 지원 (10/31 마감)",
    "2026 K-Robotics Startup Cup: 로보틱스 산업 내 예비/초기창업자를 모집합니다.(~4/5 마감)",
    "[광운대캠퍼스타운] AI 첫걸음 아카데미 (HELLO AI!) 참가자 모집 (선착순마감)",
    "AI 바우처 모집 (예산 소진 시 조기마감)",
    "AI 바우처 모집 (조기 마감될 수 있음)",
    "AI 바우처 모집 (조기마감 시 추가모집 없음)",
]


@pytest.mark.parametrize("title", CLOSED_MARKER_TITLES)
def test_closed_marker_titles_excluded(title):
    verdict = classify_keywords(make(title, source=Source.NIPA))
    assert verdict.reason == REASON_NON_ACTIONABLE, verdict


@pytest.mark.parametrize("title", DEADLINE_MENTION_TITLES)
def test_deadline_mentions_are_kept(title):
    verdict = classify_keywords(make(title, source=Source.NIPA))
    assert verdict.passed, verdict


# 제품 결정: 입찰/용역, 주관기관/운영기관 모집, 설명회, 해외 전시회/입주는 유지 (Bedrock이 판단)
ACTIONABLE_REAL = [
    ("2026년도 「AI 청년창업기업 동반성장 바우처」 지원사업 공급기업 후보군(POOL) 모집  수정공고", "AI 청년창업기업 동반성장 바우처"),
    ("2026년 AX 원스톱 바우처 지원사업 공급기업 후보군 모집 공고", "AX 원스톱 바우처 지원"),
    ("｢2026년도 AX 원스톱 바우처｣ 지원사업 수요기업 추가 모집 공고", "AX 원스톱 바우처 지원"),
    ("2026년 한-두바이 AI 사이버보안 포럼 운영 용역 모집", "해외IT지원센터(UAE) 운영"),
    ("공공데이터·AI 활용 창업경진대회 운영 용역", "사전규격공개"),
    ("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고", None),
    ("｢2026년도 AI바우처 지원사업｣ 운영기관 모집 공고", "AI바우처 지원"),
    ("국방지능형플랫폼 고도화 구축 사업설명회 안내(7.28.(화) 13:00)", "사업공고"),
    ("2026 일본 도쿄 추계 IT 전시회(JAPAN IT Week Autumn) 참가기업 모집", "공공"),
    ("하노이 IT지원센터‘26년 하반기 추가 입주기업 모집 공고", "해외IT지원센터(하노이) 운영"),
    ("『2026년도 인공지능 중심대학』 2차 선정공고", None),
    ("2027년 나노반도체 연구성과 실용화 지원사업 (혁신기업 양산시제품 개발지원) 신규과제 공고", "기술"),
]


@pytest.mark.parametrize("title, category", ACTIONABLE_REAL)
def test_actionable_items_are_kept(title, category):
    verdict = classify_keywords(make(title, source=Source.NIPA, category=category))
    assert verdict.passed, verdict


# ---------------------------------------------------------------------------
# keyword_filter 전체 흐름과 로그
# ---------------------------------------------------------------------------

def test_keyword_filter_end_to_end():
    announcements = [
        make("AI 창업 지원사업", id="1"),
        make("농업 현대화 사업", id="2"),
        make("IT 스타트업 R&D", id="3", source=Source.BIZINFO),
        make("건설업 활성화", id="4", source=Source.BIZINFO),
        make("[코리아스타트업포럼] IR데이 <The PITCH: AI Edition> 참가기업 모집", id="5"),
        make("2026년 웰컴 투 팁스 5차 참가기업 모집 (호남권)", id="6", category="사업화"),
        make("2026년 AI+S&T 혁신 기술개발(R&D) 사업 신규과제 선정결과 공고", id="7", source=Source.MSIT),
        make("에너지 효율 설비 교체 지원", id="8"),
        make("AI 제조 혁신 바우처 수요기업 모집", id="9"),
    ]
    filtered = keyword_filter(announcements)
    assert [a.id for a in filtered] == ["1", "3", "5", "9"]


def test_keyword_filter_logs_reason_counts(caplog):
    caplog.set_level(logging.INFO, logger="gov-funding-monitor")
    announcements = [
        make("AI 창업 지원사업", id="1"),
        make("부산 AI 창업 지원", id="2"),
        make("2026년 AI 사업 선정결과 공고", id="3"),
        make("농업 현대화 사업", id="4"),
        make("물류센터 운영 인력 모집", id="5"),
        make("일반 행사 안내", id="6"),
        make("에너지 AI 데이터 플랫폼 실증", id="7"),
    ]
    filtered = keyword_filter(announcements)
    assert [a.id for a in filtered] == ["1", "7"]
    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("키워드 필터링")]
    assert len(summary) == 1
    message = summary[0]
    assert "7건 → 2건" in message
    assert "지역 1건" in message
    assert "결과·예고·마감 1건" in message
    assert "제외 키워드 1건" in message
    assert "제외 키워드(약) 1건" in message
    assert "관련 키워드 없음 1건" in message
    assert "IT 신호로 제외 키워드 무시 1건" in message


# ---------------------------------------------------------------------------
# 마감일 필터 (KST 날짜 기준)
# ---------------------------------------------------------------------------

@pytest.fixture
def frozen_kst(monkeypatch):
    """relevance_filter의 KST 현재 시각 고정 (서버 UTC 시각과 무관)"""
    def freeze(value: datetime):
        monkeypatch.setattr(relevance_filter, "now_kst", lambda: value)
    monkeypatch.setenv("DEADLINE_MAX_DAYS", "61")
    return freeze


def test_deadline_d0_kstartup_item_is_kept(frozen_kst):
    """K-Startup은 크롤링 시각 + d_day로 마감일을 만든다. D-0 공고는 그날 내내 유지돼야 한다"""
    frozen_kst(datetime(2026, 10, 20, 12, 0))
    ann = make("AI 스타트업 지원", deadline=datetime(2026, 10, 20, 9, 0), d_day=0)
    assert deadline_filter([ann]) == [ann]


def test_deadline_yesterday_dropped_after_kst_midnight(frozen_kst):
    """UTC 16:00(= KST 다음날 01:00) 실행: 어제(KST) 마감 공고는 제외"""
    frozen_kst(datetime(2026, 10, 21, 1, 0))
    ann = make("AI 스타트업 지원", deadline=datetime(2026, 10, 20, 23, 59, 59))
    assert deadline_filter([ann]) == []


def test_deadline_upper_bound_by_date(frozen_kst):
    frozen_kst(datetime(2026, 10, 20, 23, 0))
    within = make("AI 1", id="1", deadline=datetime(2026, 12, 20, 23, 59, 59))  # 오늘 + 61일
    beyond = make("AI 2", id="2", deadline=datetime(2026, 12, 21, 0, 0))
    assert [a.id for a in deadline_filter([within, beyond])] == ["1"]


def test_deadline_posted_date_rule_by_date(frozen_kst):
    frozen_kst(datetime(2026, 10, 20, 0, 30))
    edge = make("AI 1", id="1", posted_date=datetime(2026, 8, 20, 18, 0))   # 오늘 - 61일 → 유지
    old = make("AI 2", id="2", posted_date=datetime(2026, 8, 19, 23, 59))
    undated = make("AI 3", id="3")
    assert [a.id for a in deadline_filter([edge, old, undated])] == ["1", "3"]


def test_deadline_filter_logs_counts(frozen_kst, caplog):
    caplog.set_level(logging.INFO, logger="gov-funding-monitor")
    frozen_kst(datetime(2026, 10, 20, 9, 0))
    items = [
        make("A", id="1", deadline=datetime(2026, 10, 19, 23, 59, 59)),
        make("B", id="2", deadline=datetime(2027, 1, 1)),
        make("C", id="3", posted_date=datetime(2026, 1, 22)),
        make("D", id="4", deadline=datetime(2026, 10, 25)),
    ]
    assert [a.id for a in deadline_filter(items)] == ["4"]
    assert any("4건 → 1건 (마감 1건, 61일 초과 1건, 공고일 초과 1건 제외)" in r.getMessage() for r in caplog.records)
