"""검색 범위 정리 HTML — 대상 사이트·키워드·경쟁사·판정 주제를 설정(config.py)에서 바로 만든다

  python -m src.aec_news.scope_report --out /tmp/aec-news-scope.html   # 파일로 저장
  python -m src.aec_news.scope_report --upload                         # 채널에 파일로 올린다 (.env 필요)

설정을 바꾼 뒤 다시 만들면 표가 항상 실제 검색과 같다 (손으로 쓴 표가 낡지 않게).
디자인은 데일리 HTML 정리본(html_report.CSS)과 같다.
"""
import argparse
import html
import re
from datetime import datetime, timezone

from .config import (
    COMPETITOR_GROUPS,
    COMPETITOR_QUERIES,
    COMPETITORS,
    KST,
    OUTLETS,
    SEARCH_TERMS,
    WATCH_TERMS,
    WEB_QUERIES,
    get_config,
)
from .html_report import CSS, HTML_LABELS
from .models import THEME_LABELS, Region

SECTION_ORDER = [Region.KR, Region.US, Region.SG, Region.GLOBAL, Region.TRADE_KR, Region.TRADE_GLOBAL]
WATCH_GROUPS = [
    ("건설 AI·에이전트", ["w_aec", "w_aec2"]),
    ("BIM·설계·도면", ["w_design", "w_design2"]),
    ("문서 AI", ["w_doc", "w_doc2"]),
    ("ConTech·스마트건설", ["w_contech", "w_contech2"]),
    ("건설 전문지용 (기사 전체가 건설)", ["w_focus", "w_focus2"]),
]
BASE_GROUPS = [("AI × 건설", "aec_ai"), ("도면·BIM", "drawing"), ("RAG", "rag"), ("건설 전문지 (AI 용어만)", "ai")]
KIND_LABELS = {"drawing": "도면·BIM", "aec_ai": "AI × 건설", "rag": "RAG", "w_aec": "건설 AI", "w_aec2": "건설 AI",
               "w_design": "BIM·설계·도면", "w_design2": "BIM·설계·도면", "w_doc": "문서 AI", "w_doc2": "문서 AI",
               "w_contech": "ConTech·스마트건설", "w_contech2": "ConTech·스마트건설"}


def _e(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _tokens(text: str) -> list[str]:
    """따옴표 안 공백·괄호는 그대로 두고 최상위 단위로 나눈다: '(a OR "b c") d' → ['(a OR "b c")', 'd']"""
    out, buf, depth, quoted = [], "", 0, False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == " " and depth == 0 and not quoted:
            if buf:
                out.append(buf)
            buf = ""
            continue
        buf += ch
    if buf:
        out.append(buf)
    return out


def _term(t: str) -> str:
    t = re.sub(r"^intitle:", "", t.strip())
    return t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t


def readable(query: str) -> str:
    """Google 검색어 → 읽기 쉬운 표현: '(intitle:A OR intitle:B) intitle:C' → 'A · B × C', 제외어는 '(제외: …)'"""
    q = re.sub(r"\bwhen:\d+d\b", "", query or "").strip()
    groups, excludes = [], []
    for tok in _tokens(q):
        if tok.startswith("-"):
            excludes.append(_term(tok[1:]))
            continue
        inner = tok[1:-1] if tok.startswith("(") and tok.endswith(")") else tok
        terms = [_term(t) for t in _tokens(inner) if t != "OR"]
        groups.append(" · ".join(terms))
    out = " × ".join(g for g in groups if g)
    if excludes:
        out += "  (제외: " + ", ".join(excludes) + ")"
    return out


def _lang_terms(kinds: list[str], lang: str) -> str:
    vals = [readable(WATCH_TERMS[k][lang]) for k in kinds if k in WATCH_TERMS and lang in WATCH_TERMS[k]]
    return "<br>".join(_e(v) for v in vals) or "—"


def build_scope_html(now: datetime) -> str:
    config = get_config()
    run = now.astimezone(KST)
    watch_count = sum(1 for o in OUTLETS.values() if o.watch)
    total_sites = len(OUTLETS)

    # 대상 사이트
    site_rows = []
    for region in SECTION_ORDER:
        outlets = [o for o in OUTLETS.values() if o.region == region]
        names = " · ".join(
            f'<span class="{"star" if o.watch else ""}">{"★" if o.watch else ""}{_e(o.name)}</span>'
            f'<small>{_e(o.domain)}</small>' for o in outlets)
        site_rows.append(f"<tr><th>{_e(HTML_LABELS[region])}</th><td class=\"n\">{len(outlets)}</td>"
                         f'<td class="sites">{names}</td></tr>')
    site_rows.append('<tr><th>기타 매체</th><td class="n">—</td><td class="sites">위 목록 밖 매체 '
                     '(매체 무관 검색으로 찾음 · 국내/해외 섹션당 점수 상위 10건)</td></tr>')

    watch_rows = "".join(
        f"<tr><th>{_e(label)}</th><td>{_lang_terms(kinds, 'ko')}</td><td>{_lang_terms(kinds, 'en')}</td></tr>"
        for label, kinds in WATCH_GROUPS)
    base_rows = "".join(
        f"<tr><th>{_e(label)}</th><td>{_e(readable(SEARCH_TERMS[k].get(Region.KR) or SEARCH_TERMS[k].get(Region.TRADE_KR)))}"
        f"</td><td>{_e(readable(SEARCH_TERMS[k].get(Region.US) or SEARCH_TERMS[k].get(Region.TRADE_GLOBAL)))}</td></tr>"
        for label, k in BASE_GROUPS)
    web_rows = "".join(
        f"<tr><td class=\"n\">{'국내' if ed == 'ko' else '영문'}</td><td class=\"n\">{days}일</td>"
        f"<td>{_e(KIND_LABELS.get(kind, kind))}</td><td>{_e(readable(q))}</td></tr>"
        for q, ed, days, kind in WEB_QUERIES)

    comp_rows = []
    for g, label in COMPETITOR_GROUPS.items():
        comps = [c for c in COMPETITORS if c.group == g]
        cells = "".join(f'<li><b>{_e(c.name)}</b><span>{_e(c.what.split(". ")[0][:90])}</span></li>' for c in comps)
        comp_rows.append(f'<div class="subgroup"><h4>{_e(label)}<span>{len(comps)}</span></h4><ul class="comp">{cells}</ul></div>')
    comp_query_rows = "".join(
        f"<tr><td class=\"n\">{'국내' if ed == 'ko' else '영문'}</td><td>{_e(', '.join(OUTLET_NAME(k) for k in keys))}</td>"
        f"<td>{_e(readable(terms))}</td></tr>" for terms, ed, keys in COMPETITOR_QUERIES)

    themes = " · ".join(_e(v) for v in THEME_LABELS.values())
    zones = "".join(f"<span>{z}</span>" for z in "ABCDEFGH")
    post = f"{config.schedule_hour:02d}:{config.schedule_minute:02d}"
    start = (datetime(2000, 1, 1, config.schedule_hour, config.schedule_minute).timestamp() - config.lead_minutes * 60)
    start_txt = datetime.fromtimestamp(start).strftime("%H:%M")
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EPC·AEC × AI 뉴스 — 검색 범위 정리 {run:%Y-%m-%d}</title>
<style>{CSS}{SCOPE_CSS}</style></head><body>
<div class="frame" id="top">
  <div class="zones">{zones}</div>
  <header class="masthead">
    <div class="mast-left">
      <p class="kicker">SEARCHDOC · NEWS MONITOR SCOPE SHEET</p>
      <h1>EPC·AEC <span class="x">×</span> AI<br><small>검색 대상 사이트 · 키워드 정리</small></h1>
      <p class="dateline">{run:%Y년 %m월 %d일} 기준 · 실제 검색 설정에서 생성</p>
    </div>
    <table class="titleblock">
      <tr><th>DRAWING NO.</th><td class="mono">AEC-SCOPE-{run:%Y%m%d}</td><th>REV.</th><td class="mono">A</td></tr>
      <tr><th>일정</th><td colspan="3">매일 {start_txt} 수집 시작 → {post} KST 게시</td></tr>
      <tr><th>대상</th><td colspan="3">사이트 {total_sites}곳 (★ 매일 확인 {watch_count}곳) + 매체 무관 검색</td></tr>
      <tr><th>경쟁사</th><td colspan="3">{len(COMPETITORS)}곳 (건설 스타트업 국내·글로벌, RAG 국내)</td></tr>
      <tr><th>채널</th><td colspan="3">#searchdoc-global-news</td></tr>
    </table>
  </header>
  <nav class="toc">
    <a href="#sites"><span class="code">S-01</span>대상 사이트</a><a href="#watch"><span class="code">S-02</span>★ 키워드</a>
    <a href="#base"><span class="code">S-03</span>기본 키워드</a><a href="#web"><span class="code">S-04</span>매체 무관 검색</a>
    <a href="#comp"><span class="code">S-05</span>경쟁사</a><a href="#rule"><span class="code">S-06</span>판정 기준</a>
  </nav>
  <main>
    <section id="sites" class="sheet-section"><header class="sec-head"><span class="code">S-01</span><h2>대상 사이트</h2>
      <span class="count">{total_sites}</span></header>
      <table class="spec"><tr><th>섹션</th><th class="n">수</th><th>매체 (★ = 매일 확인 사이트)</th></tr>{"".join(site_rows)}</table>
      <p class="note">미국·싱가포르·글로벌 30곳은 Press Gazette 'Top 50 English-language news sites'(Similarweb 2026년 10월) 순위 기준.
      ENR West는 enr.com 기사라 ENR 검색에 포함. 한국경제·매일경제·Reuters는 주요 언론 섹션에 나오지만 ★ 키워드로도 검색.</p>
    </section>
    <section id="watch" class="sheet-section accent-comp"><header class="sec-head"><span class="code">S-02</span>
      <h2>★ 매일 확인 사이트 키워드 (키워드표 + 유사 키워드 전체)</h2></header>
      <table class="spec"><tr><th>묶음</th><th>국내</th><th>해외</th></tr>{watch_rows}</table>
      <p class="note">모두 기사 제목 기준. 'A · B'는 A 또는 B, 'A × B'는 A와 B가 함께. 건설 전문지(국토일보·한국건설신문·ENR·Construction Dive·
      BD+C·Construction Executive·AEC Magazine·Architectural Record·BuiltWorlds)는 마지막 묶음으로 찾는다.</p>
    </section>
    <section id="base" class="sheet-section"><header class="sec-head"><span class="code">S-03</span>
      <h2>기본 주제 키워드 (★ 밖의 언론사)</h2></header>
      <table class="spec"><tr><th>묶음</th><th>국내</th><th>해외</th></tr>{base_rows}</table>
      <p class="note">RAG 묶음은 ★ 사이트에도 공통으로 쓴다.</p>
    </section>
    <section id="web" class="sheet-section"><header class="sec-head"><span class="code">S-04</span>
      <h2>매체 무관 검색</h2><span class="count">{len(WEB_QUERIES)}</span></header>
      <table class="spec"><tr><th class="n">판</th><th class="n">기간</th><th>묶음</th><th>검색어</th></tr>{web_rows}</table>
      <p class="note">목록 언론사 기사는 원래 섹션으로, 목록 밖 매체 기사는 기타 매체 섹션으로.</p>
    </section>
    <section id="comp" class="sheet-section accent-comp"><header class="sec-head"><span class="code">S-05</span>
      <h2>경쟁사 동향</h2><span class="count">{len(COMPETITORS)}</span></header>
      {"".join(comp_rows)}
      <table class="spec"><tr><th class="n">판</th><th>대상</th><th>검색어 (매체 제한 없음)</th></tr>{comp_query_rows}</table>
      <p class="note">동명이인이 많아 업종 단서·제외어를 붙이고, AI가 회사 소개와 대조해 그 회사 기사인지 다시 확인한다.
      제목에 이름이 없으면 원문 본문에서 이름을 확인한다(본문 언급).</p>
    </section>
    <section id="rule" class="sheet-section accent-muted"><header class="sec-head"><span class="code">S-06</span>
      <h2>판정 기준</h2></header>
      <table class="spec"><tr><th>판정 주제</th><td>{themes}</td></tr>
      <tr><th>참고 섹션</th><td>건설사·EPC사의 AI 데이터센터·전력 인프라 수주·투자 기사 (AI가 발주 수요일 뿐인 경우)</td></tr>
      <tr><th>제외</th><td>일반 AI·반도체·증시 기사, 동음이의어(EPC Power, 'AEC' 케이블 등), 이미 보낸 소식의 다른 매체 사본</td></tr></table>
    </section>
  </main>
  <footer class="colophon"><span>aec-news · 검색 범위 정리</span><span>설정이 바뀌면 다시 생성</span></footer>
</div></body></html>
"""


def OUTLET_NAME(key: str) -> str:   # noqa: N802 — 표 안에서 짧게 쓰는 도우미
    comp = next((c for c in COMPETITORS if c.key == key), None)
    return comp.name if comp else key


SCOPE_CSS = """
.spec{width:100%;border-collapse:collapse;margin:4px 0 10px;font-size:13.5px;background:#fff}
.spec th,.spec td{border:1px solid var(--rule);padding:8px 10px;text-align:left;vertical-align:top;line-height:1.55}
.spec tr:first-child th{font:600 10.5px var(--mono);letter-spacing:.12em;color:var(--ink-2);background:#f3f1ea}
.spec th{width:150px;font-weight:600;background:#faf8f2}
.spec .n{width:52px;text-align:center;font-family:var(--mono);white-space:nowrap}
.sites span{font-weight:600}.sites span.star{color:var(--orange)}
.sites small{font:10.5px var(--mono);color:var(--ink-3);margin:0 4px 0 3px}
.note{margin:6px 0 0;font-size:12.5px;color:var(--ink-2)}
ul.comp{list-style:none;margin:0 0 6px;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:8px}
ul.comp li{background:#fff;border:1px solid var(--rule);border-left:3px solid var(--orange);padding:8px 11px}
ul.comp b{display:block;font:700 14px var(--serif)}ul.comp span{font-size:12.5px;color:var(--ink-2)}
@media print{.spec{font-size:11.5px}}
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="검색 범위 정리 HTML")
    parser.add_argument("--out", default="/tmp/aec-news-scope.html")
    parser.add_argument("--upload", action="store_true", help="채널에 파일로 올린다")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    page = build_scope_html(now)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"saved {args.out} ({len(page):,} bytes)")
    if args.upload:
        from .slack_notifier import SlackNotifier
        ok = SlackNotifier().upload_file(
            page.encode("utf-8"), f"aec-news-scope-{now.astimezone(KST):%Y%m%d}.html",
            f"EPC·AEC × AI 뉴스 검색 범위 정리 {now.astimezone(KST):%Y-%m-%d}",
            comment="📋 EPC·AEC × AI 데일리 뉴스 — 검색 대상 사이트·키워드·경쟁사 정리 (HTML, 내려받아 브라우저로 열어 보세요)")
        print("uploaded" if ok else "upload FAILED")


if __name__ == "__main__":
    main()
