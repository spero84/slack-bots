"""file_reader.py 단위 테스트 — PDF/DOCX/HWPX/HWP 텍스트 추출"""
import io
import struct
import zipfile

import pytest

from src.gov_funding.utils.deadline_text import parse_deadline_from_text
from src.gov_funding.utils.file_reader import (
    _extract_text_from_para,
    _parse_hwp_records,
    detect_file_type,
    extract_text_from_docx,
    extract_text_from_file,
    extract_text_from_hwpx,
    extract_text_from_pdf,
)


# --- PDF 추출 테스트 ---


def _make_pdf_bytes(text: str) -> bytes:
    """테스트용 PDF 바이트 생성 (pdfplumber로 읽을 수 있는 형식)"""
    from pypdfium2 import PdfDocument

    pdf = PdfDocument.new()
    page = pdf.new_page(595, 842)  # A4 크기

    # pypdfium2는 텍스트 삽입이 복잡하므로 fpdf2 대신 reportlab 패턴 사용
    # 간단한 방법: pdfplumber가 읽을 수 있는 최소 PDF 직접 생성
    buf = io.BytesIO()
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


def _make_simple_pdf(text: str) -> bytes:
    """최소한의 PDF 바이트 직접 생성 (텍스트 포함)"""
    # PDF 1.4 최소 구조 — 텍스트 스트림 포함
    stream_content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET"
    stream_bytes = stream_content.encode("latin-1")
    stream_len = len(stream_bytes)

    pdf = f"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length {stream_len} >>
stream
{stream_content}
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
{str(334 + stream_len).zfill(10)} 00000 n

trailer
<< /Size 6 /Root 1 0 R >>
startxref
{399 + stream_len}
%%EOF"""
    return pdf.encode("latin-1")


def test_extract_text_from_pdf_basic():
    """PDF에서 텍스트 추출 기본 테스트"""
    pdf_bytes = _make_simple_pdf("Hello PDF World")
    result = extract_text_from_pdf(pdf_bytes)
    assert "Hello PDF World" in result


def test_extract_text_from_pdf_empty():
    """빈 PDF에서 빈 텍스트 반환"""
    # 텍스트 없는 최소 PDF
    pdf_bytes = _make_simple_pdf("")
    result = extract_text_from_pdf(pdf_bytes)
    assert isinstance(result, str)


def test_extract_text_from_pdf_invalid_bytes():
    """잘못된 바이트에서 빈 문자열 반환"""
    result = extract_text_from_pdf(b"not a pdf file")
    assert result == ""


# --- DOCX 추출 테스트 ---


def _make_docx_bytes(paragraphs: list[str], table_data: list[list[str]] | None = None) -> bytes:
    """테스트용 DOCX 바이트 생성"""
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)

    if table_data:
        table = doc.add_table(rows=len(table_data), cols=len(table_data[0]))
        for i, row_data in enumerate(table_data):
            for j, cell_text in enumerate(row_data):
                table.rows[i].cells[j].text = cell_text

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_from_docx_paragraphs():
    """DOCX 단락 텍스트 추출"""
    docx_bytes = _make_docx_bytes(["첫 번째 단락", "두 번째 단락"])
    result = extract_text_from_docx(docx_bytes)
    assert "첫 번째 단락" in result
    assert "두 번째 단락" in result


def test_extract_text_from_docx_with_table():
    """DOCX 테이블 텍스트 추출"""
    docx_bytes = _make_docx_bytes(
        ["본문 텍스트"],
        table_data=[["항목", "값"], ["이름", "테스트"]],
    )
    result = extract_text_from_docx(docx_bytes)
    assert "본문 텍스트" in result
    assert "항목" in result
    assert "테스트" in result


def test_extract_text_from_docx_empty():
    """빈 DOCX에서 빈 텍스트 반환"""
    docx_bytes = _make_docx_bytes([])
    result = extract_text_from_docx(docx_bytes)
    assert result == ""


def test_extract_text_from_docx_invalid_bytes():
    """잘못된 바이트에서 빈 문자열 반환"""
    result = extract_text_from_docx(b"not a docx file")
    assert result == ""


# --- extract_text_from_file 디스패처 테스트 ---


def test_dispatch_pdf():
    """확장자 .pdf로 PDF 추출 호출"""
    pdf_bytes = _make_simple_pdf("dispatch test")
    result = extract_text_from_file(pdf_bytes, "문서.pdf")
    assert "dispatch test" in result


def test_dispatch_docx():
    """확장자 .docx로 DOCX 추출 호출"""
    docx_bytes = _make_docx_bytes(["디스패치 테스트"])
    result = extract_text_from_file(docx_bytes, "문서.docx")
    assert "디스패치 테스트" in result


def test_dispatch_unsupported():
    """지원하지 않는 확장자는 빈 문자열 반환"""
    result = extract_text_from_file(b"data", "이미지.png")
    assert result == ""


def test_dispatch_case_insensitive():
    """확장자 대소문자 무관"""
    pdf_bytes = _make_simple_pdf("case test")
    result = extract_text_from_file(pdf_bytes, "문서.PDF")
    assert "case test" in result


# --- HWPX 추출 테스트 (메모리 내 합성 ZIP) ---

HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _make_hwpx_bytes(*paragraph_xml: str) -> bytes:
    """Contents/section0.xml 하나를 가진 최소 HWPX(zip) 생성"""
    section = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="{HP_NS}">'
        + "".join(paragraph_xml)
        + "</hs:sec>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr("Contents/section0.xml", section.encode("utf-8"))
    return buf.getvalue()


def _para(t_inner: str) -> str:
    return f"<hp:p><hp:run><hp:t>{t_inner}</hp:t></hp:run></hp:p>"


def test_hwpx_keeps_text_after_fwspace():
    """인라인 요소 뒤 텍스트(child.tail)가 빠지지 않는다 — 실제 MSS 공고문의 접수기간 형태"""
    data = _make_hwpx_bytes(_para("’26.<hp:fwSpace/>10.29.(목)"))
    text = extract_text_from_hwpx(data)
    assert "10.29.(목)" in text
    assert "’26. 10.29.(목)" in text


def test_hwpx_inline_elements_mapping():
    """lineBreak→줄바꿈, tab→탭, fwSpace/nbSpace/hwpSpace→공백, 기타 요소는 tail만 유지"""
    data = _make_hwpx_bytes(
        _para("A<hp:lineBreak/>B<hp:tab width=\"4000\" leader=\"0\" type=\"1\"/>C"
              "<hp:nbSpace/>D<hp:hwpSpace/>E<hp:fwSpace/>F<hp:markpenBegin color=\"#FFFF00\"/>G")
    )
    assert extract_text_from_hwpx(data) == "A\nB\tC D E FG"


def test_hwpx_deadline_recoverable_after_fix():
    """수정 전에는 '접수 기간 : ’26.까지'로 날짜가 사라졌다"""
    data = _make_hwpx_bytes(
        _para("□ 접수 기간 : ’26.<hp:fwSpace/>10.<hp:fwSpace/>8.(목) ~ 10.29.(목), 15:00:00까지"),
        _para("□ 지원 내용 : 사업화 자금"),
    )
    text = extract_text_from_hwpx(data)
    assert "’26. 10. 8.(목) ~ 10.29.(목)" in text
    assert parse_deadline_from_text(text).date().isoformat() == "2026-10-29"


def test_hwpx_multiple_paragraphs_and_plain_text():
    data = _make_hwpx_bytes(_para("첫 문단"), _para("둘째 문단"))
    text = extract_text_from_hwpx(data)
    assert "첫 문단" in text and "둘째 문단" in text
    assert text.index("첫 문단") < text.index("둘째 문단")


def test_hwpx_invalid_zip_returns_empty():
    assert extract_text_from_hwpx(b"not a zip") == ""


# --- HWP 5.0 바이너리 PARA_TEXT 테스트 ---


def _w(s: str) -> bytes:
    return s.encode("utf-16le")


def _ctrl(code: int, ctrl_id: bytes = b"    ") -> bytes:
    """8 WCHAR 인라인/확장 컨트롤: 코드 + 컨트롤 ID(4바이트) + 파라미터(8바이트) + 코드"""
    return struct.pack("<H", code) + ctrl_id + b"\x00" * 8 + struct.pack("<H", code)


def _ch(code: int) -> bytes:
    return struct.pack("<H", code)


def test_hwp_table_control_is_skipped_entirely():
    """표(코드 11, 'tbl ') 컨트롤 8 WCHAR가 한자 쓰레기('氠瑢')로 디코딩되지 않는다"""
    record = _w("표 앞") + _ctrl(11, b" lbt") + _w("표 뒤") + _ch(13)
    assert _extract_text_from_para(record) == "표 앞표 뒤"


def test_hwp_extended_control_21_is_skipped():
    """bizinfo 공고문 '중소湯湷기업' 재현: 코드 0x15 + 7 WCHAR 페이로드"""
    payload = b"\x2a\x6e\x77\x6e" + b"\x00" * 8 + _ch(0x15)
    record = _w("중소") + _ch(0x15) + payload + _w("기업")
    assert _extract_text_from_para(record) == "중소기업"


@pytest.mark.parametrize("code", [1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23])
def test_hwp_all_multi_wchar_controls_skip_14_bytes(code):
    record = _w("가") + _ctrl(code, b"abcd") + _w("나")
    assert _extract_text_from_para(record) == "가나"


def test_hwp_tab_control_emits_tab():
    record = _w("A") + _ctrl(9) + _w("B")
    assert _extract_text_from_para(record) == "A\tB"


def test_hwp_char_controls():
    """10/13 줄바꿈, 24 하이픈, 30/31 공백, 0/25-29 무시 (모두 1 WCHAR)"""
    record = (
        _w("A") + _ch(30) + _w("B") + _ch(31) + _w("C") + _ch(24) + _w("D")
        + _ch(10) + _w("E") + _ch(0) + _ch(25) + _ch(29) + _w("F") + _ch(13)
    )
    assert _extract_text_from_para(record) == "A B C-D\nEF"


def test_hwp_truncated_control_does_not_crash():
    record = _w("끝") + _ch(11) + b"\x00\x00"
    assert _extract_text_from_para(record) == "끝"


def test_hwp_records_para_text_tag():
    """레코드 헤더(태그 67=PARA_TEXT)를 거쳐 텍스트를 추출한다"""
    para = _w("공고 ") + _ctrl(11, b" lbt") + _w("본문") + _ch(13)
    header = 67 | (0 << 10) | (len(para) << 20)
    other = struct.pack("<I", 66 | (4 << 20)) + b"\x00" * 4  # PARA_HEADER 등 다른 레코드는 무시
    data = other + struct.pack("<I", header) + para
    assert _parse_hwp_records(data) == "공고 본문"


# --- 확장자 판별 ---


@pytest.mark.parametrize(
    "name, expected",
    [
        ("공고문.hwpx [178.7 KB]", "hwpx"),
        ("공고문.hwpx[168.11 KB]", "hwpx"),
        ("공고문.HWP", "hwp"),
        ("2026.10.20 공고.pdf", "pdf"),
        ("서식.docx", "docx"),
        ("압축.zip", None),
        ("이미지.png", None),
        ("공고.hwp_backup", None),
        ("", None),
        (None, None),
    ],
)
def test_detect_file_type(name, expected):
    assert detect_file_type(name) == expected


def test_dispatch_hwpx_with_size_suffix():
    """크기 표기가 붙은 파일명도 HWPX로 처리된다 (rsplit는 'hwpx [178.7 kb]'로 읽었다)"""
    data = _make_hwpx_bytes(_para("크기 접미사 테스트"))
    assert extract_text_from_file(data, "공고문.hwpx [178.7 KB]") == "크기 접미사 테스트"


def test_extract_text_from_pdf_reads_first_pages_only(monkeypatch):
    """수백 쪽 PDF도 앞 max_pages쪽만 파싱한다"""
    from unittest.mock import MagicMock

    from src.gov_funding.utils import file_reader

    pages = []
    for i in range(40):
        page = MagicMock()
        page.extract_text.return_value = f"page{i}"
        pages.append(page)
    pdf = MagicMock()
    pdf.pages = pages
    pdf.__enter__ = MagicMock(return_value=pdf)
    pdf.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(file_reader.pdfplumber, "open", lambda _: pdf)

    result = file_reader.extract_text_from_pdf(b"%PDF-fake", max_pages=3)

    assert result == "page0\npage1\npage2"
    assert not pages[3].extract_text.called
