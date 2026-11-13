"""데일리 다이제스트 HTML 보고서 — Slack 메시지와 같은 내용을 브라우저용 한 장짜리 문서로

Slack 스레드에 .html 파일로 올린다. 외부 글꼴·스크립트 없이 열리고(사내망·오프라인), 인쇄해도 깔끔하게 나온다.
디자인: 설계 도면 시트 — 제도용 모눈 바탕, 도면 표제란(Title block) 머리말, 섹션마다 시트 번호(C-01, K-01 …).
"""
import html
from datetime import datetime
from typing import Optional

from .config import COMPETITOR_GROUPS, COMPETITORS, KST, OUTLETS
from .models import THEME_LABELS, NewsItem, Region
from .slack_notifier import MAX_SAME_STORY_LINKS, WEEKDAYS, DigestContext, StoryGroup, group_stories

SECTION_CODES = {
    Region.KR: "K-01", Region.US: "U-01", Region.SG: "S-01", Region.GLOBAL: "G-01",
    Region.TRADE_KR: "T-01", Region.TRADE_GLOBAL: "T-02", Region.WEB_KR: "W-01", Region.WEB_GLOBAL: "W-02",
}
# HTML에서는 이모지 없이 (시트 번호가 섹션을 구분한다 — 이모지 글꼴이 없는 환경에서도 깨지지 않게)
HTML_LABELS = {
    Region.KR: "국내 주요 언론", Region.US: "미국", Region.SG: "싱가포르", Region.GLOBAL: "글로벌",
    Region.TRADE_KR: "전문지 · 국내", Region.TRADE_GLOBAL: "전문지 · 해외",
    Region.WEB_KR: "기타 매체 · 국내", Region.WEB_GLOBAL: "기타 매체 · 해외",
}
REGION_SHORT = {Region.KR: "KR", Region.US: "US", Region.SG: "SG", Region.GLOBAL: "GL", Region.TRADE_KR: "KR",
                Region.TRADE_GLOBAL: "GL", Region.WEB_KR: "KR", Region.WEB_GLOBAL: "GL"}
_COMP_BY_KEY = {c.key: c for c in COMPETITORS}


def _e(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _href(url: str) -> str:
    """http(s) 링크만 (그 밖은 '#')"""
    url = (url or "").strip()
    return _e(url) if url.startswith(("http://", "https://")) else "#"


def _day(dt: datetime) -> str:
    d = dt.astimezone(KST)
    return f"{d:%m/%d}({WEEKDAYS[d.weekday()]})"


def _story(group: StoryGroup, idx: int, kind: str) -> str:
    lead = group.lead
    meta = []
    if lead.competitor:
        comp = _COMP_BY_KEY.get(lead.competitor)
        meta.append(f'<span class="tag tag-comp">{_e(comp.name if comp else lead.competitor)}</span>')
    elif lead.theme is not None:
        meta.append(f'<span class="tag">{_e(THEME_LABELS[lead.theme])}</span>')
    meta.append(f'<span class="outlet">{_e(lead.outlet_name)}</span>')
    meta.append(f'<time>{_e(_day(lead.published_at))}</time>')
    if lead.mention == "body":
        meta.append('<span class="badge">본문 언급</span>')
    parts = [
        f'<article class="story {kind}" style="--i:{idx}">',
        f'<div class="meta">{"".join(meta)}</div>',
        f'<h3><a href="{_href(lead.link)}" target="_blank" rel="noopener">{_e(lead.title)}</a></h3>',
    ]
    if lead.summary_ko:
        parts.append(f'<p class="sum">{_e(lead.summary_ko)}</p>')
    if group.others:
        shown = group.others[:MAX_SAME_STORY_LINKS]
        links = "".join(f'<a href="{_href(o.link)}" target="_blank" rel="noopener">{_e(o.outlet_name)}</a>'
                        for o in shown)
        more = len(group.others) - len(shown)
        tail = f'<span class="more">외 {more}곳</span>' if more else ""
        parts.append(f'<div class="also"><span class="also-label">같은 소식</span>{links}{tail}</div>')
    parts.append("</article>")
    return "".join(parts)


def _section(code: str, title: str, count: int, body: str, anchor: str, accent: str = "") -> str:
    return (f'<section id="{anchor}" class="sheet-section {accent}">'
            f'<header class="sec-head"><span class="code">{_e(code)}</span><h2>{_e(title)}</h2>'
            f'<span class="count">{count}</span></header>{body}</section>')


def build_html(main_items: list[NewsItem], infra_items: list[NewsItem], ctx: DigestContext,
               competitors: Optional[list[NewsItem]] = None) -> str:
    competitors = competitors or []
    run = ctx.run_at.astimezone(KST)
    sections: list[str] = []
    nav: list[tuple[str, str, str, int]] = []   # (anchor, code, label, count)
    idx = 0

    # 🏁 경쟁사 동향
    comp_total = 0
    if competitors:
        by_group: dict[str, list[NewsItem]] = {}
        for item in competitors:
            comp = _COMP_BY_KEY.get(item.competitor or "")
            by_group.setdefault(comp.group if comp else "", []).append(item)
        blocks = []
        for g in list(COMPETITOR_GROUPS) + [k for k in by_group if k not in COMPETITOR_GROUPS]:
            if not by_group.get(g):
                continue
            stories = group_stories(by_group[g])
            comp_total += len(stories)
            cards = []
            for st in stories:
                idx += 1
                cards.append(_story(st, idx, "comp"))
            blocks.append(f'<div class="subgroup"><h4>{_e(COMPETITOR_GROUPS.get(g, "기타"))}'
                          f'<span>{len(stories)}</span></h4><div class="grid">{"".join(cards)}</div></div>')
        sections.append(_section("C-01", "경쟁사 동향", comp_total, "".join(blocks), "competitors", "accent-comp"))
        nav.append(("competitors", "C-01", "경쟁사", comp_total))

    # 국가별 / 업계지
    empty: list[str] = []
    topic_total = 0
    for region in Region:
        stories = group_stories([i for i in main_items if i.region == region])
        label = HTML_LABELS[region]
        if not stories:
            if region in (ctx.days_by_region or {}):
                empty.append(label)
            continue
        topic_total += len(stories)
        cards = []
        for st in stories:
            idx += 1
            cards.append(_story(st, idx, "topic"))
        anchor = f"r-{region.value.lower()}"
        sections.append(_section(SECTION_CODES[region], label, len(stories),
                                 f'<div class="grid">{"".join(cards)}</div>', anchor))
        nav.append((anchor, SECTION_CODES[region], label, len(stories)))

    # 📎 참고
    if infra_items:
        rows = "".join(
            f'<li><span class="flag">{_e(REGION_SHORT[i.region])}</span>'
            f'<a href="{_href(i.link)}" target="_blank" rel="noopener">{_e(i.title)}</a>'
            f'<span class="outlet">{_e(i.outlet_name)}</span><time>{_e(_day(i.published_at))}</time></li>'
            for i in sorted(infra_items, key=lambda a: -a.published_at.timestamp()))
        sections.append(_section("R-01", "참고 — AI 데이터센터·인프라 EPC/건설", len(infra_items),
                                 f'<ul class="infra">{rows}</ul>', "infra", "accent-muted"))
        nav.append(("infra", "R-01", "참고", len(infra_items)))

    if not sections:
        sections.append('<section class="sheet-section empty-day"><p>오늘은 검색 범위에서 주제에 맞는 새 기사를 '
                        '찾지 못했습니다.</p></section>')

    counts = {r: 0 for r in Region}
    for r in counts:
        counts[r] = sum(1 for o in OUTLETS.values() if o.region == r)
    domestic = counts[Region.KR]
    overseas = counts[Region.US] + counts[Region.SG] + counts[Region.GLOBAL]
    trade = counts[Region.TRADE_KR] + counts[Region.TRADE_GLOBAL]

    first_topic = next((f"#{a}" for a, c, _, _ in nav if c not in ("C-01", "R-01")), "#top")
    stats = [("경쟁사 소식", comp_total, "#competitors" if comp_total else "#top"), ("주제 기사", topic_total, first_topic),
             ("참고", len(infra_items), "#infra" if infra_items else "#top")]
    stat_html = "".join(f'<a class="stat" href="{a}"><b>{n}</b><span>{_e(label)}</span></a>'
                        for label, n, a in stats)
    nav_html = "".join(f'<a href="#{a}"><span class="code">{_e(c)}</span>{_e(l)}<em>{n}</em></a>'
                       for a, c, l, n in nav)

    notes = []
    if empty:
        notes.append(f"{' · '.join(empty)}: 새 기사 없음")
    if ctx.repeats_skipped:
        notes.append(f"이미 보냈거나 경쟁사 섹션과 같은 소식을 다른 매체가 쓴 기사 {ctx.repeats_skipped}건은 뺐습니다")
    notes += [f"⚠️ {w}" for w in ctx.warnings]
    notes_html = ("<footer class=\"notes\"><h5>NOTES</h5><ol>" + "".join(f"<li>{_e(n)}</li>" for n in notes)
                  + "</ol></footer>") if notes else ""

    title = f"EPC·AEC × AI 데일리 — {run:%Y-%m-%d}"
    zones = "".join(f"<span>{z}</span>" for z in "ABCDEFGH")
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{CSS}</style></head>
<body>
<div class="frame" id="top">
  <div class="zones">{zones}</div>
  <header class="masthead">
    <div class="mast-left">
      <p class="kicker">SEARCHDOC · MARKET INTELLIGENCE SHEET</p>
      <h1>EPC·AEC <span class="x">×</span> AI<br><small>데일리 뉴스 브리핑</small></h1>
      <p class="dateline">{run:%Y년 %m월 %d일} ({WEEKDAYS[run.weekday()]}) · {run:%H:%M} KST 발행</p>
    </div>
    <table class="titleblock">
      <tr><th>DRAWING NO.</th><td class="mono">AEC-{run:%Y%m%d}</td><th>REV.</th><td class="mono">A</td></tr>
      <tr><th>검색 기간</th><td colspan="3">{_e(ctx.period_text())}</td></tr>
      <tr><th>언론사</th><td colspan="3">국내 {domestic} · 해외 {overseas} · 전문지 {trade} + 매체 무관 검색</td></tr>
      <tr><th>경쟁사</th><td colspan="3">{len(COMPETITORS)}곳 (건설 스타트업 국내·글로벌, RAG 국내)</td></tr>
      <tr><th>키워드</th><td colspan="3">EPC·AECO × AI/RAG · BIM·설계 자동화·도면 검토 · 건설 문서 AI · 문서 AI · ConTech·스마트건설</td></tr>
    </table>
  </header>
  <div class="stats">{stat_html}</div>
  <nav class="toc">{nav_html}</nav>
  <main>{"".join(sections)}</main>
  {notes_html}
  <footer class="colophon"><span>aec-news · #searchdoc-global-news</span><span>링크는 원문 기사로 연결됩니다</span></footer>
</div>
</body></html>
"""


CSS = """
:root{
  --paper:#f5f3ec; --sheet:#fffdf8; --ink:#0f1e2e; --ink-2:#44556a; --ink-3:#7d8a99;
  --rule:#cfd5dc; --grid:rgba(29,78,137,.055); --grid-major:rgba(29,78,137,.10);
  --blue:#1d4e89; --orange:#d9480f; --orange-soft:#fff3ea; --blue-soft:#eef3fa;
  --serif:"Noto Serif KR","Nanum Myeongjo","AppleMyungjo","Batang","Source Serif Pro",Georgia,serif;
  --sans:"Pretendard","Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic","Segoe UI",sans-serif;
  --mono:"JetBrains Mono","IBM Plex Mono","D2Coding","SFMono-Regular",Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;padding:40px 20px 64px;color:var(--ink);font:15px/1.6 var(--sans);
  background-color:var(--paper);
  background-image:linear-gradient(var(--grid-major) 1px,transparent 1px),linear-gradient(90deg,var(--grid-major) 1px,transparent 1px),
    linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:48px 48px,48px 48px,8px 8px,8px 8px}
a{color:var(--blue);text-decoration:none}
a:hover{text-decoration:underline;text-underline-offset:3px}
.frame{position:relative;max-width:1120px;margin:0 auto;background:var(--sheet);
  border:2px solid var(--ink);outline:1px solid var(--ink);outline-offset:6px;padding:44px 48px 28px;
  box-shadow:0 30px 60px -40px rgba(15,30,46,.45)}
.zones{position:absolute;top:-1px;left:48px;right:48px;display:flex;justify-content:space-between;transform:translateY(-100%);
  font:10px var(--mono);color:var(--ink-3);letter-spacing:.2em;padding-bottom:10px}
.masthead{display:grid;grid-template-columns:1fr minmax(320px,420px);gap:36px;align-items:end;
  padding-bottom:26px;border-bottom:2px solid var(--ink)}
.kicker{margin:0 0 10px;font:600 11px var(--mono);letter-spacing:.18em;color:var(--orange)}
h1{margin:0;font:700 46px/1.05 var(--serif);letter-spacing:-.01em}
h1 .x{color:var(--orange);font-weight:400}
h1 small{display:block;margin-top:10px;font:500 20px var(--serif);color:var(--ink-2);letter-spacing:.02em}
.dateline{margin:14px 0 0;font:12px var(--mono);color:var(--ink-2)}
.titleblock{width:100%;border-collapse:collapse;font-size:12.5px;border:1.5px solid var(--ink)}
.titleblock th,.titleblock td{border:1px solid var(--ink);padding:6px 9px;text-align:left;vertical-align:top}
.titleblock th{width:78px;font:600 9.5px var(--mono);letter-spacing:.12em;color:var(--ink-2);background:#f3f1ea;white-space:nowrap}
.titleblock .mono{font:600 13px var(--mono)}
.stats{display:flex;gap:0;margin:22px 0 0;border:1px solid var(--rule)}
.stat{flex:1;display:flex;align-items:baseline;gap:10px;padding:14px 18px;border-right:1px solid var(--rule);color:var(--ink)}
.stat:last-child{border-right:0}
.stat b{font:700 34px/1 var(--serif)}
.stat span{font:11px var(--mono);letter-spacing:.08em;color:var(--ink-2)}
.stat:hover{background:var(--blue-soft);text-decoration:none}
.toc{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:6px;margin:18px -8px 8px;padding:10px 8px;
  background:rgba(255,253,248,.94);backdrop-filter:blur(4px);border-bottom:1px solid var(--rule)}
.toc a{display:inline-flex;align-items:center;gap:7px;padding:4px 10px 4px 4px;border:1px solid var(--rule);
  font-size:12.5px;color:var(--ink);background:var(--sheet)}
.toc a:hover{border-color:var(--ink);text-decoration:none}
.toc .code,.sec-head .code{font:700 10.5px var(--mono);letter-spacing:.06em;background:var(--ink);color:#fff;padding:2px 6px}
.toc em{font:normal 11px var(--mono);color:var(--ink-3)}
.sheet-section{margin-top:34px}
.sec-head{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.sec-head h2{margin:0;font:700 22px var(--serif)}
.sec-head .count{font:12px var(--mono);color:var(--ink-3)}
.sec-head::after{content:"";flex:1;height:0;border-top:1px dashed var(--rule);margin-left:4px}
.accent-comp .sec-head .code{background:var(--orange)}
.subgroup{margin:6px 0 22px}
.subgroup h4{display:flex;align-items:center;gap:8px;margin:0 0 10px;font:600 11.5px var(--mono);letter-spacing:.14em;color:var(--ink-2)}
.subgroup h4::before{content:"";width:14px;height:2px;background:var(--orange)}
.subgroup h4 span{color:var(--ink-3);font-weight:400}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(440px,1fr));gap:14px}
.story{position:relative;background:#fff;border:1px solid var(--rule);border-left:3px solid var(--blue);padding:14px 16px 13px;
  animation:rise .5s cubic-bezier(.2,.7,.2,1) both;animation-delay:calc(var(--i,0) * 35ms);transition:box-shadow .2s,transform .2s}
.story.comp{border-left-color:var(--orange)}
.story::before{content:"";position:absolute;top:7px;right:7px;width:9px;height:9px;
  background:linear-gradient(var(--ink-3),var(--ink-3)) center/9px 1px no-repeat,linear-gradient(var(--ink-3),var(--ink-3)) center/1px 9px no-repeat;opacity:.55}
.story:hover{box-shadow:0 10px 24px -14px rgba(15,30,46,.35);transform:translateY(-1px)}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;font:11.5px var(--mono);color:var(--ink-2);padding-right:14px}
.tag{font:600 11px var(--sans);padding:2px 8px;background:var(--blue-soft);color:var(--blue);border:1px solid rgba(29,78,137,.18)}
.tag-comp{background:var(--orange-soft);color:var(--orange);border-color:rgba(217,72,15,.22)}
.badge{font:600 10.5px var(--sans);padding:1px 7px;border:1px dashed var(--orange);color:var(--orange)}
.story h3{margin:8px 0 6px;font:700 16.5px/1.45 var(--serif);letter-spacing:-.005em}
.story h3 a{color:var(--ink)}
.story h3 a:hover{color:var(--blue)}
.sum{margin:0;color:var(--ink-2);font-size:14px}
.also{display:flex;flex-wrap:wrap;align-items:center;gap:4px 10px;margin-top:10px;padding-top:8px;border-top:1px dotted var(--rule);font-size:12.5px}
.also-label{font:600 10px var(--mono);letter-spacing:.12em;color:var(--ink-3)}
.also .more{color:var(--ink-3);font-size:12px}
.infra{list-style:none;margin:0;padding:0;border-top:1px solid var(--rule)}
.infra .flag{font:700 10px var(--mono);color:var(--ink-3);letter-spacing:.06em}
.infra li{display:grid;grid-template-columns:28px 1fr auto auto;gap:12px;align-items:baseline;padding:9px 4px;border-bottom:1px solid var(--rule);font-size:14px}
.infra .outlet,.infra time{font:11.5px var(--mono);color:var(--ink-2);white-space:nowrap}
.accent-muted .sec-head .code{background:var(--ink-3)}
.empty-day p{margin:40px 0;text-align:center;font:18px var(--serif);color:var(--ink-2)}
.notes{margin-top:40px;padding:16px 18px;border:1px solid var(--ink);background:#faf8f2}
.notes h5{margin:0 0 8px;font:700 10.5px var(--mono);letter-spacing:.2em}
.notes ol{margin:0;padding-left:20px;color:var(--ink-2);font-size:13px}
.colophon{display:flex;justify-content:space-between;margin-top:26px;padding-top:12px;border-top:2px solid var(--ink);
  font:10.5px var(--mono);letter-spacing:.08em;color:var(--ink-3)}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media (max-width:820px){
  body{padding:24px 10px}.frame{padding:28px 18px 20px;outline-offset:3px}.zones{display:none}
  .masthead{grid-template-columns:1fr}h1{font-size:34px}.grid{grid-template-columns:1fr}
  .stats{flex-wrap:wrap}.stat{min-width:45%}.infra li{grid-template-columns:24px 1fr}.infra .outlet,.infra time{grid-column:2}
}
@media (prefers-reduced-motion:reduce){.story{animation:none}html{scroll-behavior:auto}}
@media print{
  body{background:none;padding:0}.frame{box-shadow:none;outline:none;max-width:none}.toc,.zones{display:none}
  .story{animation:none;break-inside:avoid}.grid{grid-template-columns:1fr 1fr}.sheet-section{break-inside:auto}
  a{color:var(--ink)}
}
"""
