"""parse_deadline_from_text 테스트

MSIT·MOTIE는 마감일이 첨부 공고문(HWPX)에만 있다. 실제 공고문에서 추출한 텍스트
(fixtures/*/attachment_*_excerpt.txt, 아래 인라인 발췌)로 검증한다.
"""
from datetime import date, datetime
from pathlib import Path

import pytest

from src.gov_funding.utils import deadline_text
from src.gov_funding.utils.deadline_text import parse_deadline_from_text

FIXTURES = Path(__file__).parent / "fixtures"
REF = datetime(2026, 10, 20, 9, 0, 0)


def dl(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 23, 59, 59)


def parse(text, ref=REF):
    return parse_deadline_from_text(text, ref=ref)


class TestRealAttachments:
    """실제 MSIT/MOTIE 첨부 공고문 텍스트"""

    def test_msit_nsf_ires_table_with_open_start(self):
        """MSIT 3186897: '□ [중요]신청기간 | ～ ’26.11.17.(화) 17시까지' (표, 시작일 없음)

        앞쪽의 '신청 마감일 전일에', '접수마감일(주관기관 승인일) 기준' 같은 기준점 표현은 건너뛴다.
        """
        text = (FIXTURES / "msit" / "attachment_3186897_excerpt.txt").read_text(encoding="utf-8")
        assert "신청 마감일 전일에" in text  # 방해 문구가 실제로 앞에 있는지
        assert parse(text) == dl(2026, 11, 17)

    def test_motie_ax_rnd_contest_period(self):
        """MOTIE 71365: '□ 공모기간 : 2026. 10. 13(화) ～ 2026. 11. 12(목), 30일간'"""
        text = (FIXTURES / "motie" / "attachment_71365_excerpt.txt").read_text(encoding="utf-8")
        assert parse(text) == dl(2026, 11, 12)

    def test_motie_iris_registration_table(self):
        """MOTIE 71365 별첨 표: 등록기간 셀 다음 줄에 기간"""
        text = (
            "공고기간\n\n전산(iris.go.kr) 등록기간\n\n"
            "2026. 10. 13(화) ～ 2026. 11. 12(목) 17:00시 까지\n\n2026.\n\n"
            "※ 전산등록・제출 마감시간(17시) 전 까지 반드시 신청서식의 해당하는 모든 제출서류를"
        )
        assert parse(text) == dl(2026, 11, 12)

    def test_motie_supply_chain_deadline_after_schedule_table(self):
        """MOTIE 71370: 일정표(10.15∼11.16, ∼12.7)는 키워드가 없으므로 무시하고 '접수 마감' 행 사용"""
        text = (
            "～`26.12.17\n\n연번\n\n단계별\n\n기간\n\n조치사항\n\n1\n\n공고·접수\n\n"
            "10.15∼11.16\n(1개월)\n\n"
            "(재경부) 10.15일 선도사업자 선정계획 일괄 공고 → (사업자) 안정화 계획서 제출 → (부처) 접수\n\n"
            "2\n\n적격여부\n심사\n\n∼12.7\n(3주)\n\n(부처) 부처별 심사, 필요시 현장 실사 및 평가위원회 구성 \n\n"
            "3\n\n선정·통보\n\n∼12.17\n\n(부처) 최종선정 결과를 사업자 및 재경부에 통보\n"
            "   ※ 신청서 접수 마감 : ’26.11.16(월) 18:00까지 접수처(부처 담당자 이메일 등)로 제출\n"
        )
        assert parse(text) == dl(2026, 11, 16)

    def test_msit_selection_result_has_no_deadline(self):
        """MSIT 3186898 선정결과 공고 — 마감일 없음"""
        text = (
            "2026년도 「한-캐나다 산학연 공동연구사업」 신규과제 선정결과 공고\n\n"
            " 과학기술정보통신부에서는 2026년도「한-캐나다 산학연 공동연구사업」의 신규과제 "
            "선정결과를 아래와 같이 공고합니다.\n2026년 10월 16일\n"
            "<주무부처> 부총리 겸 과학기술정보통신부 장관 배 경 훈 \n□ 선정과제\n\n과제번호\n\n"
            "RS-2026-25609158\n"
        )
        assert parse(text) is None

    def test_motie_legislative_notice_has_no_deadline(self):
        """MOTIE 71374 입법예고 — 의견제출 기한은 공모 마감이 아니며 키워드 형태도 아님"""
        text = (
            "3. 의견제출\n  이 개정안에 대하여 의견이 있는 기관‧단체 또는 개인은 2026년 12월 1일까지 "
            "국민참여입법센터(http://opinion.lawmaking.go.kr)를 통하여 온라인으로 의견을 제출하시거나, "
            "다음 사항을 기재한 의견서를 산업통상부장관(참조: 엔지니어링디자인과장)에게 제출하여 주시기 바랍니다."
        )
        assert parse(text) is None


class TestDateFormats:
    @pytest.mark.parametrize("text,expected", [
        ("□ 공모기간 : 2026. 10. 13(화) ～ 2026. 11. 12(목), 30일간", dl(2026, 11, 12)),
        ("신청서 접수 마감 : ’26.11.16(월) 18:00까지", dl(2026, 11, 16)),
        ("접수기간 : '26. 10. 20.(화) ~ '26. 11. 3.(화)", dl(2026, 11, 3)),
        ("접수기간: 2026년 11월 3일까지", dl(2026, 11, 3)),
        ("제출기한 2026-11-04 18:00", dl(2026, 11, 4)),
        ("모집기간 2026/10/20 ~ 2026/11/05", dl(2026, 11, 5)),
        ("마감일시: 2026.11.30.(월) 17:00", dl(2026, 11, 30)),
        ("접수기간 : 2026. 10. 20.(화)부터 2026. 11. 3.(화)까지", dl(2026, 11, 3)),
        ("신청기간 2026.10.20 – 2026.11.06", dl(2026, 11, 6)),
    ])
    def test_formats(self, text, expected):
        assert parse(text) == expected

    def test_returns_end_of_day(self):
        result = parse("접수마감일 : 2026.11.03 18:00")
        assert (result.hour, result.minute, result.second) == (23, 59, 59)

    def test_time_and_phone_not_parsed_as_date(self):
        assert parse("접수기간 문의: 044-202-4348, 마감 시간 17:00") is None

    def test_notice_number_not_parsed_as_date(self):
        assert parse("신청기간 : 공고 제2026-643호 참조") is None


class TestYearlessDates:
    @pytest.mark.parametrize("text,expected", [
        # 시작일 연도를 종료일에 적용
        ("접수기간 : 2026. 10. 20.(화) ~ 11. 4.(수) 18:00", dl(2026, 11, 4)),
        # 물결 바로 뒤는 요일 없어도 날짜로 인정
        ("접수기간 : 2026. 10. 20.(화) ~ 11. 4. 18:00", dl(2026, 11, 4)),
        # 연말 → 연초: 종료일이 시작일보다 앞서면 다음 해
        ("모집기간: 2026.12.20 ~ 1.10.", dl(2027, 1, 10)),
        ("모집기간: 12월 20일(일) ~ 1월 10일(일)", dl(2027, 1, 10)),
        # 연도 없는 단일 날짜는 기준일 연도
        ("마감일시: 11. 4.(수) 18:00", dl(2026, 11, 4)),
        ("접수 마감일: 11월 4일 18시", dl(2026, 11, 4)),
    ])
    def test_resolved_relative_to_previous_date_or_ref(self, text, expected):
        assert parse(text) == expected

    def test_rolls_over_to_next_year_near_year_end(self):
        """기준일(12월)보다 6개월 넘게 과거인 연도 없는 날짜는 다음 해"""
        assert parse("접수 마감일: 1월 5일 18시", ref=datetime(2026, 12, 15)) == dl(2027, 1, 5)

    def test_weekday_selects_year(self):
        """요일이 맞는 연도를 고른다 (2026-11-04는 수요일, 2027-11-04는 목요일)"""
        assert parse("마감일시: 11. 4.(수) 18:00", ref=datetime(2027, 1, 10)) == dl(2026, 11, 4)

    def test_accepts_date_as_ref(self):
        assert parse("마감일시: 11. 4.(수)", ref=date(2026, 10, 20)) == dl(2026, 11, 4)

    def test_default_ref_is_now_kst(self, monkeypatch):
        monkeypatch.setattr(deadline_text, "now_kst", lambda: datetime(2026, 12, 15, 9, 0))
        assert parse_deadline_from_text("접수 마감일: 1월 5일 18시") == dl(2027, 1, 5)


class TestSelectionRules:
    def test_range_prefers_end_date(self):
        assert parse("신청기간: 2026.10.20 ~ 2026.11.30") == dl(2026, 11, 30)

    def test_open_ended_range_is_unknown(self):
        """종료일 없는 기간은 마감일을 알 수 없음"""
        assert parse("신청기간: 2026.10.20(화) ~ 예산 소진 시까지") is None
        assert parse("접수기간 : 2026. 10. 20.(화)부터 상시 접수") is None

    def test_no_date(self):
        assert parse("접수기간 상시, 예산 소진 시 조기 마감") is None

    def test_single_date_without_range(self):
        assert parse("제출기한: 2026. 11. 3.(화) 18:00까지 (선정발표 2026. 12. 1.)") == dl(2026, 11, 3)

    def test_multi_round_uses_first_round(self):
        text = "□ 접수기간\n ○ 1차: 2026.10.20 ~ 10.31.\n ○ 2차: 2026.11.10 ~ 11.20."
        assert parse(text) == dl(2026, 10, 31)

    def test_first_keyword_with_date_wins(self):
        text = (
            "□ 공모기간 : 2026. 10. 13(화) ～ 2026. 11. 12(목)\n"
            "□ 서류 제출기한 : 2026. 11. 20.(금)"
        )
        assert parse(text) == dl(2026, 11, 12)

    @pytest.mark.parametrize("distractor", [
        "접수마감일 기준 최근 3년(2023.1.1.~2025.12.31.) 매출액",
        "접수 마감일 현재 2025.12.31. 결산 기준 부채비율",
        "신청 마감일 전일(2025.12.31.)까지 참여제한이 종료된 자",
        "접수 마감일까지 ‘25년도 결산이 마무리되지 않은 기관(2025.12.31. 기준)",
        "신청기간 내 2025.12.31. 이전 서류 제출",
    ])
    def test_reference_point_phrases_are_skipped(self, distractor):
        """'마감일 기준/현재/전일/까지', '기간 내'는 마감일 정의가 아니므로 건너뛴다"""
        text = f"{distractor}\n□ 신청기간 : 2026. 10. 20.(화) ~ 2026. 11. 3.(화) 18:00"
        assert parse(text) == dl(2026, 11, 3)

    def test_window_stops_at_next_section(self):
        """'□' 다음 항목(사업기간 등)의 날짜를 접수기간으로 오인하지 않는다"""
        assert parse("□ 접수기간 : 추후 공지\n□ 사업기간 : 2026.12.01 ~ 2027.11.30") is None

    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_empty(self, text):
        assert parse_deadline_from_text(text) is None
