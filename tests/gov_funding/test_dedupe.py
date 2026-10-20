"""공고 중복 제거 테스트 (canon_title, 유사 제목 규칙, deduplicate_announcements)

병합/비병합 쌍은 2026-10 진단의 실제 저장 제목(S3 Vectors 672건)과 10/10 중복 게시 사례에서 가져왔다.
"""
import logging
from datetime import date, datetime

import pytest

import random
import time

from src.gov_funding.storage import (
    TitleMatcher,
    canon_title,
    deduplicate_announcements,
    is_similar_title,
)
from src.gov_funding.storage import s3_storage
from src.gov_funding.storage.models import Announcement, Source


def make(title, source, id=None, **kwargs):
    return Announcement(
        id=id or f"{source.value}-{abs(hash(title)) % 10**8}",
        source=source,
        title=title,
        url=f"https://example.com/{source.value}/{id or abs(hash(title)) % 10**8}",
        **kwargs,
    )


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(s3_storage, "today_kst", lambda: date(2026, 10, 20))


# ---------------------------------------------------------------------------
# canon_title
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    # 가운뎃점 변형 (ㆍ U+318D는 \w라서 기존 정규화로 지워지지 않았다)
    ("2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고 (공정ㆍ응용 솔루션 AX R&D)",
     "2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고 (공정·응용 솔루션 AX R&D)"),
    ("실증‧확산사업", "실증∙확산사업"),
    ("실증•확산사업", "실증・확산사업"),
    ("실증･확산사업", "실증확산사업"),
    # 년도
    ("2026년도 AI기반 가상융합산업육성사업 신규과제 모집공고", "2026년 AI기반 가상융합산업육성사업 신규과제 모집공고"),
    # 따옴표/괄호 (내용은 유지)
    ("「독자 AI 파운데이션 모델 프로젝트」 공동활용 데이터 지원사업 공고", "독자 AI 파운데이션 모델 프로젝트 공동활용 데이터 지원사업 공고"),
    ("｢2026년도 AX 원스톱 바우처｣ 지원사업수요기업 모집 공고", "「2026년도 AX 원스톱 바우처」지원사업 수요기업 모집 공고"),
    ("2027년 초격차 스타트업 프로젝트 정부 첫 실증ㆍ구매 (프로젝트) 주관기관 모집공고",
     "2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고"),
    # 맨 앞 상태 태그
    ("(수정공고) 2026년도 오픈소스 AI·SW 개발·활용 지원사업 신규과제 모집 공고", "2026년 오픈소스 AI·SW 개발·활용 지원사업 신규과제 모집 공고(수정)"),
    ("(재공고) 온두라스 AI 기반 개인 맞춤형 복지 안내 및 상담서비스 PoC 구현", "온두라스 AI 기반 개인 맞춤형 복지 안내 및 상담서비스 PoC 구현"),
    ("(공모) 2026년도 AI허브 학습용데이터(추론용) 사업공고", "2026년도 AI허브 학습용데이터(추론용) 사업공고"),
    ("[재공고] 2026년 SW 고성장클럽 모집", "2026년 SW 고성장클럽 모집"),
    # 끝의 상투어
    ("「2026 데이터+AI 혁신 챌린지」 통합 공고 안내", "「2026 데이터+AI 혁신 챌린지」 통합 공고"),
    ("2026년 AI허브 인공지능 학습용 데이터 업사이클링 사업 공고문", "2026년 AI허브 인공지능 학습용 데이터 업사이클링 사업 재공고"),
    ("2026년 예비창업패키지 예비창업자 모집 수정 공고", "2026년 예비창업패키지 예비창업자 모집공고"),
    ("『2026 K-보안 스타트업 일본 시장개척단』 참가기업 모집 공고", "『2026 K-보안 스타트업 일본 시장개척단』 참가기업 모집 공고(연장공고)"),
    ("2026년도 차세대광기반연산반도체핵심기술개발사업 신규과제 재공모", "2026년도 차세대광기반연산반도체핵심기술개발사업 신규과제 공모"),
    # 공백/대소문자/전각
    ("2024 AI창업지원사업", "[2024] AI 창업 지원사업"),
    ("ＡＩ 바우처 지원사업", "ai 바우처 지원사업"),
])
def test_canon_title_equal(a, b):
    assert canon_title(a) == canon_title(b)


@pytest.mark.parametrize("a, b", [
    ("2026년도 2차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고", "2026년 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고"),
    ("｢2026년도 AX 원스톱 바우처｣ 지원사업수요기업 모집 공고", "｢2026년도 AX 원스톱 바우처｣ 지원사업 수요기업 추가 모집 공고"),
    ("2026년 AX 원스톱 바우처 지원사업 공급기업 후보군 모집 공고", "2026년 AX 원스톱 바우처 지원사업 수요기업 모집 공고"),
    ("2026년 AI 바우처 지원사업", "2027년 AI 바우처 지원사업"),
])
def test_canon_title_different(a, b):
    assert canon_title(a) != canon_title(b)


def test_canon_title_examples():
    assert canon_title("「2026 데이터+AI 혁신 챌린지」 통합 공고 안내") == "2026데이터ai혁신챌린지통합"
    assert canon_title("2024년 [AI] 스타트업 지원사업 (2차)") == "2024년ai스타트업지원사업2차"
    assert canon_title("(재공고) 2026년도 AI 바우처 모집 공고") == "2026년ai바우처"


def test_canon_title_edge_cases():
    assert canon_title("") == ""
    assert canon_title(None) == ""
    assert canon_title("공고") == "공고"  # 전부 지워지면 원래 값 유지
    assert canon_title("---") == "---"


def test_normalized_title_uses_canon_title():
    bizinfo = make("2026년 AI 역기능 대응 기술 실증ㆍ확산사업(R&D연계형) 공고", Source.BIZINFO)
    msit = make("2026년 AI 역기능 대응 기술 실증‧확산사업(R&D연계형) 공고", Source.MSIT)
    assert bizinfo.normalized_title == msit.normalized_title == canon_title(bizinfo.title)
    assert "normalized_title" in bizinfo.model_dump()


# ---------------------------------------------------------------------------
# 유사 제목 규칙
# ---------------------------------------------------------------------------

MERGE_PAIRS = [
    # 10/10 실제 중복 게시 (K-Startup vs MSS)
    ("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 주관기관 모집공고",
     "2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고"),
    ("2027년 초격차 스타트업 프로젝트 정부 첫 실증ㆍ구매 (프로젝트) 주관기관 모집공고",
     "2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고"),
    ("「2026 데이터+AI 혁신 챌린지」 통합 공고 안내", "「2026 데이터+AI 혁신 챌린지」 통합 공고"),
    ("2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고 (공정ㆍ응용 솔루션 AX R&D, 응용 제품 AX R&D)",
     "2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고 (공정·응용 솔루션 AX R&D, 응용 제품 AX R&D)"),
    # 한쪽에만 있는 연도
    ("AX실증밸리조성(R&D)사업 신규 지원 대상과제 공고", "2026년도 AX실증밸리조성(R&D) 사업 신규지원 대상과제 공고"),
    ("2026년도 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고", "2026년 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고"),
    # 어순/띄어쓰기/상투어 차이 (실제 교차 소스 저장 제목)
    ("2026년 2차 정보보호핵심원천기술개발사업 신규지원 대상과제 공고", "2026년도 정보보호핵심원천기술개발사업 2차 신규지원 대상과제 공고"),
    ("2026년도 과학기술정보통신부「고성능컴퓨팅지원」사업 사용자 추가모집 공고", "2026년도 과학기술정보통신부「고성능 컴퓨팅 지원」 사용자 추가 모집 공고"),
    ("2026년 AI-반도체 해외실증 지원 사업(서버형) 공고", "2026년 AI-반도체 해외실증 지원 사업 신규과제(서버형) 공고"),
    ("공공 병원정보시스템 AI 클라우드서비스 개발 검증 지원", "공공 병원정보시스템 AI 클라우드서비스 개발 검증 지원 사업 공모"),
    ("「2026년 오픈데이터포럼(ODF) 공공데이터·AI 활용 리빙랩 프로젝트」 과제 공모 안내", "2026년 오픈데이터포럼(ODF) 공공데이터·AI 활용 리빙랩 프로젝트"),
    # 모집 상투어(참여 기업 모집 공고)와 띄어쓰기 차이 — 2026-10-20 dry-run에서 따로 게시될 뻔한 쌍 (K-Startup vs 기업마당)
    ("2026년 서초창업스테이션 프리-오픈이노베이션", "2026년 서초창업스테이션 프리 오픈 이노베이션 참여 기업 모집 공고"),
    ("2026 TEL VENTURE CAPITAL X 충남창조경제혁신센터 Nexus Connect 투자 밋업 데이",
     "2026년 TEL VENTURE CAPITAL X 충남창조경제혁신센터 Nexus Connect 투자 밋업 데이 참여기업 모집 공고"),
    ("2026년 「의료 데이터 스페이스 구축」 과제 공모", "「의료 데이터 스페이스 구축」 사업 공고"),
    # 어순 차이 ('3차'의 위치), 복합어 차이 ('조성 사업' vs '조성사업')
    ("2026년 3차 소공인 클린제조환경조성 사업 모집 공고", "2026년 소공인 클린제조환경조성사업 3차 모집 공고"),
    ("2026년 1차 나주형 청년 에너지 창업 사관학교(직ㆍ주ㆍ락ㆍ비) 사업 수행기업 모집 공고",
     "2026년 나주형 청년 에너지 창업 사관학교(직·주·락·비) 사업 1차 수행기업 모집"),
]

NO_MERGE_PAIRS = [
    # 회차 차이
    ("2026년도 2차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고", "2026년 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고"),
    # 공급기업 vs 수요기업
    ("2026년 AX 원스톱 바우처 지원사업 공급기업 후보군 모집 공고", "｢2026년도 AX 원스톱 바우처｣ 지원사업수요기업 모집 공고"),
    # 같은 사업의 다른 트랙
    ("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 주관기관 모집공고", "2027년 초격차 스타트업 프로젝트 기술사업화 및 투자유치 주관기관 모집공고"),
    ("2026년도 딥테크 특화 창업중심대학 창업기업 모집공고", "2026년도 딥테크 특화 창업중심대학 추천형 창업기업 모집공고"),
    # 결과 공고의 단계 차이
    ("2026년 산업전문인력 AI역량강화 지원사업 발표평가 결과 및 지원 대상과제 선정 공고",
     "2026년 산업전문인력 AI역량강화 지원사업 서류평가 결과 및 발표평가 대상자 선정 공고"),
    # 추가 모집은 다른 차수
    ("｢2026년도 AX 원스톱 바우처｣ 지원사업수요기업 모집 공고", "｢2026년도 AX 원스톱 바우처｣ 지원사업 수요기업 추가 모집 공고"),
    ("2026년도 과학기술정보통신부「고성능컴퓨팅지원」사업 사용자 모집 공고", "2026년도 과학기술정보통신부「고성능 컴퓨팅 지원」 사용자 추가 모집 공고"),
    # 유사도 0.76 인 다른 사업 (ai-news 0.6 기준이면 병합됨)
    ("2026년 2차 정보보호핵심원천기술개발사업 신규지원 대상과제 공고", "2026년도 제2차 로봇산업기술개발사업 신규지원 대상과제 공고"),
    # 연도/숫자 차이
    ("2026년 AI 바우처 지원사업 수요기업 모집", "2027년 AI 바우처 지원사업 수요기업 모집"),
    ("5G 특화망 실증 지원사업 참여기업 모집", "6G 특화망 실증 지원사업 참여기업 모집"),
    ("2026년 AI 모델 벤치마크 데이터셋 구축 사업(1차)", "2026년 AI 모델 벤치마크 데이터셋 구축 사업(2차)"),
    # 짧은 제목
    ("AI 바우처", "AI 바우처 사업"),
    # 상투어('기업', '참여')를 빼도 남는 단어가 다르면 병합하지 않는다
    ("2026년 AI 바우처 공급 기업 모집 공고", "2026년 AI 바우처 수요 기업 모집 공고"),
    ("2026년 중소기업 AI 도입 지원사업 참여기업 모집", "2026년 중견기업 AI 도입 지원사업 참여기업 모집"),
    ("2026년 AI 바우처 참여기업 모집", "2026년 AI 바우처 참여기관 모집"),
    ("2026년 서초창업스테이션 프리-오픈이노베이션", "2026년 서초창업스테이션 오픈이노베이션 데모데이 참여 기업 모집 공고"),
]


@pytest.mark.parametrize("a, b", MERGE_PAIRS)
def test_similar_titles_merge(a, b):
    assert is_similar_title(a, b)
    assert is_similar_title(b, a)


@pytest.mark.parametrize("a, b", NO_MERGE_PAIRS)
def test_similar_titles_do_not_merge(a, b):
    assert not is_similar_title(a, b)
    assert not is_similar_title(b, a)


# ---------------------------------------------------------------------------
# deduplicate_announcements
# ---------------------------------------------------------------------------

class TestDeduplicate:
    def test_same_title_keeps_higher_priority(self):
        kstartup = [make("2024년 AI 창업 지원사업", Source.KSTARTUP, "k1")]
        bizinfo = [make("2024년 AI 창업 지원사업", Source.BIZINFO, "b1")]
        result = deduplicate_announcements(kstartup, bizinfo)
        assert [(a.source, a.id) for a in result] == [(Source.KSTARTUP, "k1")]

    def test_different_titles_kept(self):
        result = deduplicate_announcements(
            [make("AI 창업 지원", Source.KSTARTUP, "k1")],
            [make("IT 스타트업 육성", Source.BIZINFO, "b1")],
        )
        assert len(result) == 2

    def test_normalized_title_comparison(self):
        result = deduplicate_announcements(
            [make("[2024] AI 창업 지원사업", Source.KSTARTUP, "k1")],
            [make("2024 AI창업지원사업", Source.BIZINFO, "b1")],
        )
        assert len(result) == 1

    def test_middle_dot_variants_dedupe(self):
        """기업마당 'ㆍ' vs MOTIE '·' 실제 사례"""
        bizinfo = [make("2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고 (공정ㆍ응용 솔루션 AX R&D, 응용 제품 AX R&D)",
                        Source.BIZINFO, "b1")]
        motie = [make("2026년 지역거점 AX 혁신 기술개발 사업 신규지원 대상과제 공고 (공정·응용 솔루션 AX R&D, 응용 제품 AX R&D)",
                      Source.MOTIE, "m1")]
        result = deduplicate_announcements(bizinfo, motie)
        assert [a.id for a in result] == ["b1"]

    def test_ten_ten_near_duplicate_merges_across_sources(self, caplog):
        caplog.set_level(logging.INFO, logger="gov-funding-monitor")
        kstartup = [make("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 주관기관 모집공고", Source.KSTARTUP, "179428")]
        mss = [
            make("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고", Source.MSS, "1071648"),
            make("2027년 초격차 스타트업 프로젝트 기술사업화 및 투자유치 주관기관 모집공고", Source.MSS, "1071649"),
        ]
        result = deduplicate_announcements(kstartup, mss)
        assert [(a.source, a.id) for a in result] == [(Source.KSTARTUP, "179428"), (Source.MSS, "1071649")]
        merge_logs = [r for r in caplog.records if r.levelno == logging.INFO and "중복 제거 (유사 제목" in r.getMessage()]
        assert len(merge_logs) == 1
        assert "https://example.com/mss/1071648" in merge_logs[0].getMessage()  # 버린 공고의 URL도 남긴다
        summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("중복 제거 완료")]
        assert "동일 제목 0건, 유사 제목 1건 병합" in summary[0]

    def test_fuzzy_rule_never_merges_same_source(self):
        kstartup = [
            make("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 주관기관 모집공고", Source.KSTARTUP, "1"),
            make("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고", Source.KSTARTUP, "2"),
        ]
        assert len(deduplicate_announcements(kstartup)) == 2

    def test_exact_canon_merges_within_same_source(self):
        """같은 소스의 같은 공고 (페이지 경계 중복, 공고/수정공고)는 정확 일치로 병합"""
        nipa = [
            make("2026년도 AI 통합 바우처(클라우드 바우처) 지원 사업 수정공고", Source.NIPA, "16664"),
            make("2026년도 AI 통합 바우처(클라우드 바우처) 지원 사업 공고", Source.NIPA, "16518"),
            make("2026년도 AI 통합 바우처(클라우드 바우처) 지원 사업 수정공고", Source.NIPA, "16664"),
        ]
        result = deduplicate_announcements(nipa)
        assert [a.id for a in result] == ["16664"]

    @pytest.mark.parametrize("a, b", NO_MERGE_PAIRS)
    def test_no_merge_pairs_kept_across_sources(self, a, b):
        result = deduplicate_announcements([make(a, Source.KSTARTUP, "1")], [make(b, Source.MSS, "2")])
        assert len(result) == 2

    @pytest.mark.parametrize("a, b", MERGE_PAIRS)
    def test_merge_pairs_merged_across_sources(self, a, b):
        result = deduplicate_announcements([make(a, Source.MSIT, "1")], [make(b, Source.NIPA, "2")])
        assert [(a.source, a.id) for a in result] == [(Source.MSIT, "1")]

    def test_priority_order_and_order_preserved(self):
        kstartup = [make("A 사업 공고", Source.KSTARTUP, "k1"), make("B 스타트업 모집", Source.KSTARTUP, "k2")]
        nipa = [make("C 바우처 공고", Source.NIPA, "n1"), make("A 사업 공고", Source.NIPA, "n2")]
        mss = [make("B 스타트업 모집 공고", Source.MSS, "m1"), make("D 데이터 사업", Source.MSS, "m2")]
        result = deduplicate_announcements(kstartup, nipa, mss)
        assert [a.id for a in result] == ["k1", "k2", "n1", "m2"]

    def test_expired_kept_item_is_replaced_by_live_duplicate(self):
        """우선순위 높은 쪽이 이미 마감됐으면 진행 중인 재공고를 남긴다 (마감일 필터가 뒤에서 지우므로)"""
        nipa = [make("2026년 AI허브 인공지능 학습용 데이터 업사이클링 사업 공고", Source.NIPA, "n1",
                     deadline=datetime(2026, 10, 1, 23, 59, 59))]
        msit = [make("2026년 AI허브 인공지능 학습용 데이터 업사이클링 사업 재공고", Source.MSIT, "m1",
                     deadline=datetime(2026, 10, 30, 23, 59, 59))]
        result = deduplicate_announcements(nipa, msit)
        assert [(a.source, a.id) for a in result] == [(Source.MSIT, "m1")]

    def test_expired_replacement_also_applies_to_fuzzy_merge(self):
        kstartup = [make("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 주관기관 모집공고", Source.KSTARTUP, "k1",
                         deadline=datetime(2026, 10, 19, 23, 59, 59))]
        mss = [make("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고", Source.MSS, "m1",
                    deadline=datetime(2026, 10, 20, 18, 0))]  # 오늘(KST) 마감은 유효
        result = deduplicate_announcements(kstartup, mss)
        assert [a.id for a in result] == ["m1"]

    def test_live_kept_item_is_not_replaced(self):
        nipa = [make("2026년 AI 바우처 지원사업 공고", Source.NIPA, "n1", deadline=datetime(2026, 10, 25))]
        msit = [make("2026년 AI 바우처 지원사업 재공고", Source.MSIT, "m1", deadline=datetime(2026, 11, 30))]
        undated = [make("2026년 AI 바우처 지원사업", Source.MSS, "s1")]
        result = deduplicate_announcements(nipa, msit, undated)
        assert [a.id for a in result] == ["n1"]

    def test_empty_titles_are_not_merged(self):
        result = deduplicate_announcements([make("", Source.MSIT, "1")], [make("", Source.MOTIE, "2")])
        assert len(result) == 2

    def test_real_cross_source_groups(self):
        """실제 저장 제목: 교차 소스 중복 그룹이 하나씩 남는다"""
        kstartup = [make("2026년도 예비창업패키지 예비창업자 모집 수정 공고", Source.KSTARTUP, "176458"),
                    make("2026년도 창업패키지(AI 인재 실증형) 창업기업 모집공고", Source.KSTARTUP, "177670")]
        bizinfo = [make("2026년 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고", Source.BIZINFO, "b1"),
                   make("2026년 K-온디바이스 AI반도체 기술개발사업 신규 지원대상 연구개발과제 공고", Source.BIZINFO, "b2")]
        nipa = [make("2026년 AI기반 가상융합산업육성사업 신규과제 모집공고", Source.NIPA, "16505")]
        msit = [make("2026년도 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고", Source.MSIT, "3186879"),
                make("2026년도 AI기반 가상융합산업육성사업 신규과제 모집공고", Source.MSIT, "3186511"),
                make("2026년도 2차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고", Source.MSIT, "3186751")]
        mss = [make("2026년 예비창업패키지 예비창업자 모집공고", Source.MSS, "1065929"),
               make("2026년 창업패키지(AI 인재 실증형) 창업기업 모집공고", Source.MSS, "1068297")]
        motie = [make("2026년도 K-온디바이스AI반도체기술개발사업 신규 지원대상 연구개발과제 공고", Source.MOTIE, "2026426")]
        result = deduplicate_announcements(kstartup, bizinfo, nipa, msit, mss, motie)
        assert [a.id for a in result] == ["176458", "177670", "b1", "b2", "16505", "3186751"]

    def test_signature_accepts_empty_lists(self):
        assert deduplicate_announcements() == []
        assert deduplicate_announcements([], []) == []


# ---------------------------------------------------------------------------
# TitleMatcher (실행 간 '이미 본 사업' 조회)
# ---------------------------------------------------------------------------

STORED = [
    ("2026년 서초창업스테이션 프리 오픈 이노베이션 참여 기업 모집 공고", {"key": "bizinfo_PBLN_1"}),
    ("2026년도 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고", {"key": "msit_1"}),
    ("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 프로젝트 주관기관 모집공고", {"key": "mss_1"}),
    ("2026년 AX 원스톱 바우처 지원사업 공급기업 후보군 모집 공고", {"key": "nipa_1"}),
    ("", {"key": "empty"}),
]


class TestTitleMatcher:
    def test_exact_and_fuzzy_lookup(self):
        matcher = TitleMatcher(STORED)
        assert len(matcher) == 4  # 빈 제목은 제외
        # 정규 제목 정확 일치 (년도/공백/상투어 차이)
        assert matcher.find("2026년 3차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고") == {"key": "msit_1"}
        # 유사 규칙
        assert matcher.find("2026년 서초창업스테이션 프리-오픈이노베이션") == {"key": "bizinfo_PBLN_1"}
        assert matcher.find("2027년 초격차 스타트업 프로젝트 정부 첫 실증·구매 주관기관 모집공고") == {"key": "mss_1"}

    @pytest.mark.parametrize("title", [
        "2026년도 2차 디지털혁신기술국제공동연구사업 신규지원 대상과제 공고",   # 회차 차이
        "｢2026년도 AX 원스톱 바우처｣ 지원사업수요기업 모집 공고",            # 수요기업
        "2027년 초격차 스타트업 프로젝트 기술사업화 및 투자유치 주관기관 모집공고",
        "완전히 다른 AI 공고",
        "",
        None,
    ])
    def test_no_match(self, title):
        assert TitleMatcher(STORED).find(title) is None

    def test_match_details_and_exact_beats_fuzzy(self):
        matcher = TitleMatcher([
            ("2026년 서초창업스테이션 프리 오픈 이노베이션 참여 기업 모집 공고", "fuzzy"),
            ("2026년 서초창업스테이션 프리-오픈이노베이션", "exact"),
        ])
        found = matcher.match("2026년 서초창업스테이션 프리-오픈이노베이션 공고")
        assert found.payload == "exact" and found.exact and found.ratio == 1.0
        found = matcher.match("2026 서초창업스테이션 프리 오픈이노베이션 참가기업 모집")
        assert not found.exact
        assert found.ratio >= s3_storage.FUZZY_RATIO_THRESHOLD

    def test_same_canon_first_inserted_wins(self):
        matcher = TitleMatcher([("2026년 AI 바우처 지원사업 공고", 1), ("2026년도 AI 바우처 지원사업 재공고", 2)])
        assert matcher.find("2026년 AI 바우처 지원사업") == 1

    def test_best_ratio_wins(self, monkeypatch):
        """유사 후보가 여럿이면 유사도가 가장 높은 항목"""
        ratios = {"후보 하나 AI 데이터 플랫폼 실증": 0.92, "후보 둘 AI 데이터 플랫폼 실증": 0.97}

        def fake_ratio(candidate, key):
            return next((r for t, r in ratios.items() if s3_storage.canon_title(t) == candidate.canon), None)

        monkeypatch.setattr(s3_storage, "_fuzzy_ratio", fake_ratio)
        matcher = TitleMatcher((t, t) for t in ratios)
        assert matcher.find("후보 셋 AI 데이터 플랫폼 실증") == "후보 둘 AI 데이터 플랫폼 실증"

    def test_consistent_with_is_similar_title(self):
        stored = [a for a, _ in MERGE_PAIRS] + [a for a, _ in NO_MERGE_PAIRS]
        matcher = TitleMatcher((t, i) for i, t in enumerate(stored))
        for query in [b for _, b in MERGE_PAIRS] + [b for _, b in NO_MERGE_PAIRS]:
            found = matcher.find(query)
            expected = [i for i, t in enumerate(stored) if is_similar_title(query, t)]
            if expected:
                assert found in expected, query
            else:
                assert found is None, query

    def test_lookup_speed(self):
        """오늘 신규 150건 × 저장 1,000건 조회가 1초 안에 끝난다 (숫자 없는 제목 = 같은 후보 묶음인 최악의 경우)"""
        rng = random.Random(7)
        words = ("인공지능 데이터 클라우드 플랫폼 바우처 스타트업 글로벌 진출 오픈이노베이션 실증 확산 고도화 "
                 "컨설팅 투자유치 디지털 전환 보안 반도체 로봇 메타버스 헬스케어 핀테크 교육 인재양성 "
                 "창업도약 성장 스케일업 수출 해외 협력").split()
        stored = [f"2026년 {' '.join(rng.sample(words, rng.randint(3, 6)))} 지원사업 참여기업 모집 공고"
                  for _ in range(1000)]
        queries = [f"{' '.join(rng.sample(words, rng.randint(3, 6)))} 사업 모집" for _ in range(140)]
        queries += [title.replace("참여기업 모집 공고", "참가 기업 모집") for title in stored[:10]]
        start = time.perf_counter()
        matcher = TitleMatcher((t, i) for i, t in enumerate(stored))
        results = [matcher.find(q) for q in queries]
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"{elapsed:.2f}s"
        assert all(r is not None for r in results[-10:])
