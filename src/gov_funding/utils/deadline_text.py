"""공고문 본문/첨부파일 텍스트에서 마감일 추출

MSIT·MOTIE처럼 목록·상세 HTML에는 마감일이 없고 첨부 공고문(HWPX/PDF) 안에만
'신청기간 ... ～ ’26.11.17.(화) 17시까지', '공모기간 : 2026. 10. 13(화) ～ 2026. 11. 12(목)'
형태로 적혀 있는 경우에 사용한다.

규칙
- 마감 관련 키워드(접수/신청/공모/모집/제출/등록 + 기간/기한/마감, 마감일/마감일시 등) 뒤
  160자 창에서 날짜를 찾는다. '접수 마감일 기준', '신청 마감일 전일'처럼 키워드가 기준점으로만
  쓰인 경우는 건너뛴다.
- 지원 형식: 2026.11.17 / 2026-11-17 / 2026. 11. 17. / ’26.11.17. / 2026년 11월 17일 /
  연도 없는 11월 17일, 11. 17.(화), '~ 11. 17.' (연도는 앞 날짜 → 없으면 기준일로 보정)
- 기간이면 종료일(물결/부터 뒤 첫 날짜)을 마감일로 본다. '2026.10.20 ~ 예산 소진 시'처럼
  종료일이 없으면 마감일을 알 수 없으므로 None.
- 날짜가 있는 첫 키워드 창이 결정한다 (공고문 상단 = 본 접수기간). 여러 차수면 첫 차수 마감.
- 반환값은 다른 크롤러와 같이 naive datetime 23:59:59 (KST 기준).
"""
import re
from datetime import date, datetime, timedelta
from typing import Optional, Union

from .dates import now_kst

# 마감 관련 키워드: 접수기간, 신청 기간, 공모기간, 모집기간, 제출기한, 접수 마감, 접수마감일, 마감일시 ...
_KEYWORD = re.compile(
    r"(?:접수|신청|공모|모집|제출|등록)\s*(?:기간|기한|마감\s*(?:일시|일자|일|기한|시한)?)"
    r"|마감\s*(?:일시|일자|일|기한|시한)"
)

# 키워드가 '마감일 기준', '마감일 전일', '신청기간 내' 처럼 기준점으로만 쓰인 경우
_REFERENCE_CONTEXT = re.compile(
    r"\s*(?:기준|현재|전일|전날|전까지|이전|이후|으로부터|로부터|부터|임박|연장|까지|동안"
    r"|(?:내|중)(?=$|\s|에|[,.)]))"
)

_WINDOW = 160
# 상위 항목 기호(□, ■ 등)가 나오면 다른 항목이므로 창을 자른다
_SECTION_BREAK = re.compile(r"[□■▣◆◇]")

_DATE = re.compile(
    # 2026.11.17 / 2026-11-17 / 2026. 11. 17 / 2026년 11월 17일
    r"(?<!\d)(?P<y>20\d{2})\s*[.\-/년]\s*(?P<m>\d{1,2})\s*[.\-/월]\s*(?P<d>\d{1,2})(?!\d)"
    # ’26.11.17 / '26. 11. 17
    r"|[’'‘`´](?P<yy>\d{2})\s*[.\-/년]\s*(?P<yy_m>\d{1,2})\s*[.\-/월]\s*(?P<yy_d>\d{1,2})(?!\d)"
    # 물결 바로 뒤 연도 없는 날짜: '~ 11. 3.' (요일 없어도 기간 종료일로 확실)
    r"|(?<=[~～∼〜])\s*(?P<t_m>\d{1,2})\s*\.\s*(?P<t_d>\d{1,2})(?![\d:])"
    # 연도 없는 한글 날짜: 11월 17일
    r"|(?<![\d.])(?P<k_m>\d{1,2})\s*월\s*(?P<k_d>\d{1,2})\s*일"
    # 연도 없는 점 날짜는 요일이 붙은 경우만: 11. 17.(화) — 목차 번호(1. 2.)와 구분
    r"|(?<![\d.])(?P<p_m>\d{1,2})\s*\.\s*(?P<p_d>\d{1,2})\s*\.?\s*(?=\(\s*[월화수목금토일])"
)
_WEEKDAY = re.compile(r"\s*\.?\s*\(\s*([월화수목금토일])")
_WEEKDAYS = "월화수목금토일"

_TILDE = r"[~～∼〜]"
_OPEN_START_BEFORE = re.compile(_TILDE + r"\s*$")
_RANGE_BETWEEN = re.compile(_TILDE + r"|부터|\s[-–—]\s|[–—]")
_RANGE_AFTER = re.compile(r"^[^~～∼〜]{0,25}?(?:" + _TILDE + r"|부터)")


def _to_datetime(year: int, month: int, day: int) -> Optional[datetime]:
    try:
        return datetime(year, month, day, 23, 59, 59)
    except ValueError:
        return None


def _resolve_yearless(
    month: int, day: int, weekday: Optional[int], prev: Optional[datetime], ref: datetime
) -> Optional[datetime]:
    """연도 없는 날짜의 연도 보정

    - 요일이 있으면 요일이 맞는 연도 (앞 날짜 연도 → 다음 해 → 전년 순으로 확인)
    - 앞 날짜가 있으면 그 이후가 되는 연도 ('2026.12.20 ~ 1.10' → 2027-01-10)
    - 없으면 기준일 연도, 기준일보다 6개월 넘게 과거면 다음 해
    """
    base = prev.year if prev else ref.year
    options = {y: _to_datetime(y, month, day) for y in (base, base + 1, base - 1)}
    options = {y: dt for y, dt in options.items() if dt is not None}
    if not options:
        return None

    if weekday is not None:
        for y in (base, base + 1, base - 1):
            dt = options.get(y)
            if dt is not None and dt.weekday() == weekday:
                return dt

    same, nxt = options.get(base), options.get(base + 1)
    if prev is not None:
        if same is not None and same >= prev:
            return same
        return nxt or same
    if same is not None and same < ref - timedelta(days=183) and nxt is not None:
        return nxt
    return same or nxt


def _dates_in_window(window: str, ref: datetime) -> list[tuple[int, int, datetime]]:
    """창 안의 날짜 목록 [(시작 위치, 끝 위치, datetime)]"""
    dates: list[tuple[int, int, datetime]] = []
    prev: Optional[datetime] = None
    for m in _DATE.finditer(window):
        wd_match = _WEEKDAY.match(window, m.end())
        weekday = _WEEKDAYS.index(wd_match.group(1)) if wd_match else None

        if m.group("y"):
            dt = _to_datetime(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        elif m.group("yy"):
            dt = _to_datetime(2000 + int(m.group("yy")), int(m.group("yy_m")), int(m.group("yy_d")))
        else:
            month = m.group("t_m") or m.group("k_m") or m.group("p_m")
            day = m.group("t_d") or m.group("k_d") or m.group("p_d")
            dt = _resolve_yearless(int(month), int(day), weekday, prev, ref)

        if dt is None:
            continue
        dates.append((m.start(), m.end(), dt))
        prev = dt
    return dates


def _deadline_in_window(window: str, ref: datetime) -> tuple[bool, Optional[datetime]]:
    """창 하나에서 마감일 결정

    Returns:
        (결정 여부, 마감일). 날짜가 없으면 (False, None) → 다음 키워드로 넘어간다.
        종료일 없는 기간('2026.10.20 ~ 예산 소진 시')이면 (True, None).
    """
    dates = _dates_in_window(window, ref)
    if not dates:
        return False, None

    prev_end = 0
    for i, (start, end, dt) in enumerate(dates):
        gap = window[prev_end:start]
        is_range_end = (
            _OPEN_START_BEFORE.search(gap) if i == 0 else _RANGE_BETWEEN.search(gap)
        )
        if is_range_end:
            return True, dt
        prev_end = end

    # 시작일 뒤에 물결/부터만 있고 종료일이 없음 → 마감일 미상
    if _RANGE_AFTER.search(window[dates[0][1]:]):
        return True, None
    return True, dates[0][2]


def parse_deadline_from_text(
    text: Optional[str], ref: Optional[Union[datetime, date]] = None
) -> Optional[datetime]:
    """공고문 텍스트에서 마감일 추출

    Args:
        text: 상세 페이지 본문 + 첨부파일에서 추출한 텍스트
        ref: 연도 없는 날짜('11. 17.(화)') 보정 기준 시각 (기본: 현재 KST)

    Returns:
        마감일 (해당 날짜 23:59:59) 또는 None (키워드/날짜 없음, 종료일 미상)
    """
    if not text:
        return None

    if ref is None:
        ref = now_kst()
    elif not isinstance(ref, datetime):
        ref = datetime(ref.year, ref.month, ref.day)

    flat = re.sub(r"\s+", " ", text)
    for kw in _KEYWORD.finditer(flat):
        if _REFERENCE_CONTEXT.match(flat, kw.end()):
            continue
        window = flat[kw.end(): kw.end() + _WINDOW]
        section = _SECTION_BREAK.search(window)
        if section:
            window = window[: section.start()]

        decided, deadline = _deadline_in_window(window, ref)
        if decided:
            return deadline
    return None
