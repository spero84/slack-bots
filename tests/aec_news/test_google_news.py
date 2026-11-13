"""aec-news Google News 검색 테스트 (쿼리 길이, 언론사 호스트 필터, 날짜 필터, 차단 감지, 원문 URL 복원)

네트워크를 타지 않도록 RSS/batchexecute 응답을 픽스처로 쓴다.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from src.aec_news import google_news as gn
from src.aec_news.config import MAX_QUERY_CHARS, OUTLETS, SEARCH_TERMS
from src.aec_news.models import Region, Theme

from .conftest import make_item

NOW = datetime(2026, 11, 12, 23, 30, tzinfo=timezone.utc)   # 11/13 08:30 KST
ALL_DAYS = {Region.KR: 2, Region.US: 7, Region.SG: 7, Region.GLOBAL: 7}


def rss(*items: tuple[str, str, str, str, str]) -> bytes:
    """items: (title, source_url, source_title, pubDate, guid)"""
    body = "".join(
        f"""<item><title>{t}</title><link>https://news.google.com/rss/articles/{g}?oc=5</link>
        <guid isPermaLink="false">{g}</guid><pubDate>{d}</pubDate>
        <description>&lt;a href="https://news.google.com/rss/articles/{g}?oc=5"&gt;{t}&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;{st}&lt;/font&gt;</description>
        <source url="{su}">{st}</source></item>"""
        for t, su, st, d, g in items
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


def pub(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%a, %d %b %Y %H:%M:%S GMT")


# --- 쿼리 ---

def test_every_query_fits_google_length_limit():
    queries = gn.build_queries(ALL_DAYS)
    assert queries
    assert max(len(q.q) for q in queries) <= MAX_QUERY_CHARS
    assert all(q.q.endswith(f"when:{q.days}d") for q in queries)


def test_every_outlet_is_searched_for_every_theme():
    queries = gn.build_queries(ALL_DAYS)
    for kind, by_region in SEARCH_TERMS.items():
        for region in by_region:
            covered = {k for q in queries if q.kind == kind and q.region == region for k in q.outlet_keys}
            expected = {k for k, o in OUTLETS.items() if o.region == region}
            assert covered == expected, (kind, region)
    # 점검 쿼리: 국내·미국·싱가포르 1개, 글로벌은 지역판별(en-GB, en-IN) 2개
    assert sorted(q.region.value for q in queries if q.kind == "health") == ["GLOBAL", "GLOBAL", "KR", "SG", "US"]


def test_site_groups_share_one_edition():
    for q in gn.build_queries(ALL_DAYS):
        editions = {OUTLETS[k].search_edition for k in q.outlet_keys}
        assert editions == {q.edition}, q


def test_outlet_counts_and_unique_hosts():
    from collections import Counter
    counts = Counter(o.region for o in OUTLETS.values())
    assert counts[Region.KR] == 10
    assert counts[Region.US] + counts[Region.SG] + counts[Region.GLOBAL] == 30
    hosts = [h for o in OUTLETS.values() for h in o.keep_hosts]
    assert len(hosts) == len(set(hosts))
    domains = [o.domain for o in OUTLETS.values()]
    assert len(domains) == len(set(domains))


def test_indian_and_uk_outlets_use_local_editions():
    assert OUTLETS["bbc"].search_edition == "en-GB"
    assert OUTLETS["indiatimes"].search_edition == "en-IN"
    assert OUTLETS["nytimes"].search_edition == "en-US"
    assert OUTLETS["straitstimes"].search_edition == "en-SG"
    assert OUTLETS["indiatimes"].name_for("economictimes.indiatimes.com") == "The Economic Times"


def test_seven_day_window_does_not_break_length_limit():
    queries = gn.build_queries({r: 7 for r in Region})
    assert max(len(q.q) for q in queries) <= MAX_QUERY_CHARS


def test_pack_outlets_splits_and_rejects_impossible():
    outlets = [o for o in OUTLETS.values() if o.region == Region.KR]
    groups = gn.pack_outlets("intitle:AI", outlets, 2, max_chars=120)
    assert sum(len(g) for g in groups) == len(outlets)
    assert all(len(gn._compose("intitle:AI", g, 2)) <= 120 for g in groups)
    with pytest.raises(ValueError):
        gn.pack_outlets("x" * 200, outlets, 2)


def test_single_outlet_query_has_no_parentheses():
    q = gn._compose("intitle:AI", [OUTLETS["theedge"]], 7)
    assert q == "intitle:AI site:theedgesingapore.com when:7d"


def test_query_url_uses_region_edition():
    q = gn.SearchQuery("drawing", Region.SG, "BIM site:straitstimes.com when:7d", ("straitstimes",), 7, "en-SG")
    assert "hl=en-SG" in q.url and "gl=SG" in q.url and "ceid=SG%3Aen" in q.url


# --- 파싱 / 필터 ---

def kr_query(kind="drawing"):
    return gn.SearchQuery(kind, Region.KR, "q", ("donga",), 2)


def test_parse_feed_filters_hosts_dates_language():
    content = rss(
        ("쪼개 읽던 산업 도면, AI가 통째로 읽는다 - 동아일보", "https://www.donga.com", "동아일보", pub(5), "A1"),
        ("인포시즈·서울대, 산업 도면 통째로 읽는 AI - IT조선", "https://it.chosun.com", "IT조선", pub(3), "A2"),
        ("야구 게시판 도면 - MLBPARK", "https://mlbpark.donga.com", "MLBPARK", pub(2), "A3"),      # 하위 도메인
        ("오래된 도면 AI 기사 - 동아일보", "https://www.donga.com", "동아일보", pub(24 * 30), "A4"),  # 기간 밖
        ("Drawing AI in English - Seoul Economic Daily", "https://www.sedaily.com", "서울경제", pub(1), "A5"),  # 영문 제목
        ("Untitled - 한국경제", "https://www.hankyung.com", "한국경제", pub(1), "A6"),               # 빈 제목
        ("도면 AI 다른 매체 - 전자신문", "https://www.etnews.com", "전자신문", pub(1), "A7"),        # 목록 밖
    )
    stat = gn.QueryStat("KR", "drawing", "donga")
    items = gn.parse_feed(content, kr_query(), NOW - timedelta(days=2, hours=2), NOW, stat)
    assert [i.google_id for i in items] == ["A1", "A2"]
    assert items[0].title == "쪼개 읽던 산업 도면, AI가 통째로 읽는다"
    assert items[0].outlet_name == "동아일보" and items[0].outlet_key == "donga"
    assert items[1].outlet_name == "IT조선" and items[1].outlet_key == "chosun"
    assert items[0].query_themes == [Theme.DRAWING_BIM]
    assert items[0].published_at.tzinfo is not None
    assert stat.raw == 7 and stat.kept == 2 and stat.stale == 1


def test_clean_title_strips_doubled_suffix():
    assert gn.clean_title("서치독, CES 혁신상 - 머니투데이 - 머니투데이", "머니투데이") == "서치독, CES 혁신상"
    assert gn.clean_title("A &amp; B - Reuters", "Reuters") == "A & B"
    assert gn.clean_title("Title - With Dash", "Forbes") == "Title - With Dash"


def test_us_feed_drops_korean_titles_and_sponsored_hosts():
    query = gn.SearchQuery("aec_ai", Region.US, "q", ("bloomberg",), 7)
    content = rss(
        ("AI Comes to Construction - Bloomberg", "https://www.bloomberg.com", "Bloomberg", pub(10), "B1"),
        ("Sponsored AI Construction - Bloomberg Media", "https://sponsored.bloomberg.com", "Bloomberg Media", pub(10), "B2"),
        ("AI Construction Councils - Forbes", "https://councils.forbes.com", "Forbes Councils", pub(10), "B3"),
        ("건설 AI 한글 제목 - Bloomberg", "https://www.bloomberg.com", "Bloomberg", pub(10), "B4"),
    )
    items = gn.parse_feed(content, query, NOW - timedelta(days=7), NOW, gn.QueryStat("US", "aec_ai", ""))
    assert [i.google_id for i in items] == ["B1"]


def test_health_query_counts_but_keeps_nothing():
    query = gn.SearchQuery("health", Region.KR, "q", ("donga",), 1)
    content = rss(("AI 기사 - 동아일보", "https://www.donga.com", "동아일보", pub(1), "H1"))
    stat = gn.QueryStat("KR", "health", "")
    assert gn.parse_feed(content, query, NOW - timedelta(days=1), NOW, stat) == []
    assert stat.raw == 1


def test_parse_feed_rejects_html_page():
    with pytest.raises(ValueError):
        gn.parse_feed(b"<html><body>Before you continue</body></html", kr_query(), NOW, NOW,
                      gn.QueryStat("KR", "drawing", ""))


# --- 클라이언트 (간격, 재시도, 차단) ---

class FakeResp:
    def __init__(self, status=200, text="", url="https://news.google.com/rss/search?q=x", content=None,
                 ctype="application/xml"):
        self.status_code = status
        self.text = text
        self.url = url
        self.content = content if content is not None else text.encode()
        self.headers = {"Content-Type": ctype}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, timeout=None, **kwargs):
        self.calls.append((method, url, kwargs))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(responses):
    sleeps = []
    session = FakeSession(responses)
    client = gn.GoogleNewsClient(session=session, interval=0, sleep=sleeps.append)
    return client, session, sleeps


def test_client_trips_on_429_and_refuses_further_calls():
    client, session, _ = make_client([FakeResp(429)])
    with pytest.raises(gn.GoogleThrottled):
        client.request("GET", "https://news.google.com/rss/search?q=a")
    assert client.tripped == "HTTP 429"
    with pytest.raises(gn.GoogleThrottled):
        client.request("GET", "https://news.google.com/rss/search?q=b")
    assert len(session.calls) == 1   # 차단 후에는 요청하지 않는다


def test_client_trips_on_sorry_redirect():
    client, _, _ = make_client([FakeResp(200, url="https://www.google.com/sorry/index?continue=x")])
    with pytest.raises(gn.GoogleThrottled):
        client.request("GET", "https://news.google.com/rss/search?q=a")
    assert "sorry" in client.tripped


def test_client_retries_network_error_then_succeeds():
    client, session, sleeps = make_client([requests.ConnectionError("reset"), FakeResp(500), FakeResp(200, "ok")])
    resp = client.request("GET", "https://news.google.com/rss/search?q=a")
    assert resp.status_code == 200
    assert len(session.calls) == 3
    assert sleeps == list(gn.RETRY_BACKOFF)


def test_client_paces_requests():
    t = [100.0]
    sleeps = []

    def fake_sleep(sec):
        sleeps.append(sec)
        t[0] += sec

    session = FakeSession([FakeResp(200), FakeResp(200)])
    client = gn.GoogleNewsClient(session=session, interval=3.0, sleep=fake_sleep, monotonic=lambda: t[0])
    client.request("GET", "u1")
    client.request("GET", "u2")
    assert len(sleeps) == 1 and 3.0 <= sleeps[0] <= 4.0


def test_search_all_stops_after_throttle_and_warns():
    responses = [FakeResp(200, content=rss(("도면 AI 기사 - 동아일보", "https://www.donga.com", "동아일보", pub(1), "Z1"))),
                 FakeResp(429)]
    client, session, _ = make_client(responses)
    result = gn.search_all(NOW, ALL_DAYS, client=client)
    assert len(session.calls) == 2
    assert [i.google_id for i in result.items] == ["Z1"]
    assert result.failed == result.total - 1 and not result.complete
    assert any("차단" in w for w in result.warnings)


def test_search_all_warns_on_stale_flood_and_empty_health():
    old = rss(*[(f"도면 AI 옛 기사 {i} - 동아일보", "https://www.donga.com", "동아일보", pub(24 * 400), f"O{i}")
                for i in range(6)])
    empty = rss()
    queries = gn.build_queries(ALL_DAYS)
    responses = [FakeResp(200, content=old if n == 0 else empty) for n in range(len(queries))]
    client, _, _ = make_client(responses)
    result = gn.search_all(NOW, ALL_DAYS, client=client)
    assert result.complete
    assert result.items == []
    assert any("검색 기간 조건이 무시" in w for w in result.warnings)
    assert any("점검 검색 결과 0건" in w for w in result.warnings)


def test_lookback_days_extends_after_gap():
    from src.aec_news.config import get_config
    cfg = get_config()
    assert gn.lookback_days(Region.KR, None, NOW, cfg) == 2
    assert gn.lookback_days(Region.US, None, NOW, cfg) == 7
    assert gn.lookback_days(Region.KR, NOW - timedelta(days=1), NOW, cfg) == 2
    assert gn.lookback_days(Region.KR, NOW - timedelta(days=3, hours=1), NOW, cfg) == 5
    assert gn.lookback_days(Region.KR, NOW - timedelta(days=30), NOW, cfg) == 7


# --- 원문 URL 복원 ---

ARTICLE_PAGE = '<c-wiz><div jscontroller="x" data-n-a-sg="SIG123" data-n-a-ts="1794470000"></div></c-wiz>'


def batch_body(url: str) -> str:
    inner = json.dumps(["garturlres", url, 1])
    return ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]])


def test_parse_batchexecute_reads_canonical_not_amp():
    inner = json.dumps(["garturlres", "https://www.donga.com/news/1", 1, "https://www.donga.com/amp/1"])
    body = ")]}'\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]])
    assert gn.parse_batchexecute(body) == "https://www.donga.com/news/1"
    rejected = ")]}'\n" + json.dumps([["wrb.fr", "Fbv4je", None, None, None, [3], "generic"]])
    assert gn.parse_batchexecute(rejected) is None


def test_decode_posts_signature_from_article_page():
    client, session, _ = make_client([FakeResp(200, ARTICLE_PAGE), FakeResp(200, batch_body("https://www.donga.com/news/1"))])
    url = client.decode("https://news.google.com/rss/articles/CBMiAU_yqLabc?oc=5")
    assert url == "https://www.donga.com/news/1"
    method, post_url, kwargs = session.calls[1]
    assert method == "POST" and post_url == gn.BATCH_URL
    freq = json.loads(kwargs["data"]["f.req"])
    assert '"CBMiAU_yqLabc",1794470000,"SIG123"' in freq[0][0][1]
    assert session.calls[0][1] == "https://news.google.com/rss/articles/CBMiAU_yqLabc"


def test_decode_without_signature_returns_none():
    client, _, _ = make_client([FakeResp(200, "<html>changed</html>")])
    assert client.decode("https://news.google.com/rss/articles/CBMiAU_yqLabc") is None


def test_article_id_and_non_google_urls():
    assert gn.article_id("https://news.google.com/rss/articles/CBMiXYZ?oc=5") == "CBMiXYZ"
    assert gn.article_id("https://www.donga.com/news/1") is None


def test_resolve_urls_uses_cache_filters_paths_and_hosts():
    a = make_item(1, outlet_key="donga")
    b = make_item(2, outlet_key="usatoday", outlet_name="USA Today", region=Region.US)
    c = make_item(3, outlet_key="forbes", outlet_name="Forbes", region=Region.US)
    d = make_item(4, outlet_key="mt", outlet_name="머니투데이")
    cache = {
        a.google_id: "https://www.donga.com/news/1",
        b.google_id: "https://www.usatoday.com/press-release/story/1/x/",
        c.google_id: "https://www.forbes.com/councils/forbestechcouncil/2026/11/06/x/",
        d.google_id: "https://www.mt.co.kr/amp/future/2026/11/10/1",
    }
    client, session, _ = make_client([])
    kept, warnings = gn.resolve_urls([a, b, c, d], cache, client=client)
    assert session.calls == []                       # 전부 캐시
    assert [i.google_id for i in kept] == [a.google_id, c.google_id, d.google_id]   # 보도자료 경로 제외
    assert c.outlet_name == "Forbes (Councils 기고)"
    assert d.url == "https://www.mt.co.kr/future/2026/11/10/1"
    assert warnings == []


def test_resolve_urls_drops_foreign_host_and_keeps_google_link_on_failure():
    a = make_item(1, outlet_key="mk", outlet_name="매일경제")
    b = make_item(2, outlet_key="donga")
    cache = {a.google_id: "https://stock.mk.co.kr/news/view/1"}   # 원문이 시세 하위 도메인
    client, _, _ = make_client([FakeResp(200, "<html></html>")])   # b 복원 실패
    kept, warnings = gn.resolve_urls([a, b], cache, client=client)
    assert [i.google_id for i in kept] == [b.google_id]
    assert kept[0].url is None and kept[0].link == b.google_url
    assert warnings and "복원 실패" in warnings[0]


def test_resolve_urls_respects_decode_cap():
    items = [make_item(i) for i in range(1, 4)]
    client, session, _ = make_client([FakeResp(200, "<html></html>")])
    gn.resolve_urls(items, {}, client=client, max_decodes=1)
    assert len(session.calls) == 1


def test_unverified_usatoday_item_is_deferred_when_throttled():
    usa = make_item(1, outlet_key="usatoday", outlet_name="USA Today", region=Region.US)
    nyt = make_item(2, outlet_key="nytimes", outlet_name="The New York Times", region=Region.US)
    client, session, _ = make_client([])
    client.tripped = "HTTP 429"
    kept, warnings = gn.resolve_urls([usa, nyt], {}, client=client)
    assert [i.google_id for i in kept] == [nyt.google_id]       # USA Today는 경로 확인 전까지 보내지 않는다
    assert kept[0].url is None
    assert session.calls == []
    assert any("차단" in w for w in warnings)


def test_usatoday_item_past_decode_cap_is_deferred():
    items = [make_item(1, outlet_key="donga"),
             make_item(2, outlet_key="usatoday", outlet_name="USA Today", region=Region.US)]
    client, _, _ = make_client([FakeResp(200, ARTICLE_PAGE), FakeResp(200, batch_body("https://www.donga.com/news/1"))])
    kept, _ = gn.resolve_urls(items, {}, client=client, max_decodes=1)
    assert [i.google_id for i in kept] == ["CBMi0001"]


def test_stale_canary_uses_sent_when_window_not_hours_filter():
    # --hours 1 → when:1d. 13~24시간 전 기사는 when:1d 안이라 '기간 무시'가 아니다
    query = gn.SearchQuery("drawing", Region.KR, "q", ("donga",), 1)
    content = rss(*[(f"도면 AI {i} - 동아일보", "https://www.donga.com", "동아일보", pub(20), f"S{i}") for i in range(6)])
    stat = gn.QueryStat("KR", "drawing", "")
    gn.parse_feed(content, query, NOW - timedelta(hours=1), NOW, stat)
    assert stat.raw == 6 and stat.stale == 0 and stat.kept == 0


# --- 경쟁사 검색 ---

from src.aec_news.config import COMPETITORS  # noqa: E402

COMP = {c.key: c for c in COMPETITORS}


def test_competitor_queries_fit_and_cover_every_company():
    queries = gn.build_competitor_queries(7)
    assert max(len(q.q) for q in queries) <= MAX_QUERY_CHARS
    assert {k for q in queries for k in q.outlet_keys} == set(COMP)
    assert all("site:" not in q.q for q in queries)
    assert {q.edition for q in queries} == {"ko", "en-US"}
    assert len(COMPETITORS) >= 16 and len({c.key for c in COMPETITORS}) == len(COMPETITORS)


def comp_query(keys=("infocz",), edition="ko"):
    region = Region.KR if edition == "ko" else Region.GLOBAL
    return gn.SearchQuery("competitor", region, "q", keys, 7, edition)


def test_competitor_feed_accepts_any_outlet_but_blocks_noise():
    content = rss(
        ("인포시즈, 서울대와 산업 도면 특화 AI모델 개발 - AI타임스", "https://www.aitimes.com", "AI타임스", pub(5), "C1"),
        ("인포시즈, 한전과 전력망 AI 공개 - 머니투데이", "https://www.mt.co.kr", "머니투데이", pub(6), "C2"),
        ("인포시즈 직원 블로그 - 브런치", "https://brunch.co.kr", "브런치스토리", pub(6), "C3"),
        ("인포시즈 주가 - 매일경제 마켓", "https://stock.mk.co.kr", "매일경제 마켓", pub(6), "C4"),
        ("Infocz unveils AI - The Elec", "https://www.thelec.net", "The Elec", pub(6), "C5"),   # 영어 제목도 받는다
    )
    items = gn.parse_feed(content, comp_query(), NOW - timedelta(days=7, hours=2), NOW, gn.QueryStat("KR", "c", ""))
    assert [i.google_id for i in items] == ["C1", "C2", "C5"]
    assert items[0].outlet_key == "web:www.aitimes.com" and items[0].outlet_name == "AI타임스"
    assert items[1].outlet_key == "mt" and items[1].region == Region.KR
    assert all(i.competitor_hits == ["infocz"] for i in items)
    assert items[0].query_themes == []


def test_multi_company_query_hints_all_candidates():
    content = rss(("Egnyte Unveils Context Studio - Yahoo Finance", "https://finance.yahoo.com", "Yahoo Finance",
                   pub(10), "E1"))
    items = gn.parse_feed(content, comp_query(("trunktools", "egnyte", "wyreai", "beamai"), "en-US"),
                          NOW - timedelta(days=7), NOW, gn.QueryStat("GLOBAL", "c", ""))
    assert items[0].competitor_hits == ["trunktools", "egnyte", "wyreai", "beamai"]
    assert items[0].region == Region.GLOBAL


def test_tag_competitors_uses_only_distinctive_names():
    items = [
        make_item(1, title="올거나이즈의 일본 상장, 투자사가 꼽은 최대 난관"),
        make_item(2, title="BHSN과 계약 검토 AI 협력"),
        make_item(3, title="Attentive Releases New AI Pro"),          # 다른 회사 Attentive
        make_item(4, title="Attentive.ai buys Takeoff Monkey"),
        make_item(5, title="New provision for AI in construction contracts"),   # 일반 명사 — 태그하지 않음
        make_item(6, title="Viktor Orbán on AI"),
        make_item(7, title="Infosys 실적 발표"),
    ]
    gn.tag_competitors(items)
    assert [i.competitor_hits for i in items] == [["allganize"], ["bhsn"], [], ["beamai"], [], [], []]


def test_names_pattern_is_literal_and_hangul_safe():
    pat = gn.names_pattern(COMP["beamai"])
    assert pat.search("Attentive.ai makes first acquisition")
    assert not pat.search("Attentive ai")                         # '.'은 리터럴
    assert gn.names_pattern(COMP["bhsn"]).search("BHSN과 협력")      # 한글 조사가 붙어도
    assert not gn.names_pattern(COMP["bhsn"]).search("ABHSNX")


def test_resolve_urls_competitor_mode_keeps_press_release():
    item = make_item(1, outlet_key="usatoday", outlet_name="USA Today", region=Region.US)
    cache = {item.google_id: "https://www.usatoday.com/press-release/story/1/trunk-tools-raises/"}
    kept, _ = gn.resolve_urls([item], cache, client=make_client([])[0], competitor_mode=True)
    assert kept == [item] and item.url.endswith("trunk-tools-raises/")


def test_sponsored_path_is_labelled():
    item = make_item(1, outlet_key="web:www.constructiondive.com", outlet_name="Construction Dive", region=Region.GLOBAL)
    cache = {item.google_id: "https://www.constructiondive.com/spons/ai-takeoff/1/"}
    kept, _ = gn.resolve_urls([item], cache, client=make_client([])[0], competitor_mode=True)
    assert kept[0].outlet_name == "Construction Dive (스폰서 콘텐츠)"


def test_video_pages_of_new_outlets_are_not_articles():
    for url in ("https://www.cbc.ca/player/play/video/9.7413617", "https://www.aljazeera.com/video/inside-story/1",
                "https://www.nbcnews.com/now/video/ai-construction-123", "https://www.cbsnews.com/video/x/"):
        assert gn.is_non_article_path(url), url
    assert not gn.is_non_article_path("https://www.bbc.com/news/articles/c1234")
    assert not gn.is_non_article_path("https://www.cnbc.com/2026/10/31/ai-construction-sites.html")


def test_new_competitor_tags_avoid_known_homonyms():
    titles = {
        "올리비에 두아즈 내한 공연": [], "김영태 두아즈 대표 기고": ["doaz"],
        "그래파이트 공장 증설": [], "그래파이의 아카식DB 출시": ["graphai"],
        "ABB-사이오닉 로봇 의수": [], "사이오닉AI, PepperOCR-VL 1위": ["sionic"], "Sionic and Agentix payments": [],
        "와이즈넛, 쇼핑엔티 AI 쇼핑 에이전트 수주": ["wisenut"],
        "Document Crunch adds AI review for construction drawings": ["documentcrunch"],
        "SWAPP.AI automates construction documents": ["swapp"], "Mark Swapp obituary": [],
    }
    items = [make_item(i, title=t) for i, t in enumerate(titles, 1)]
    gn.tag_competitors(items)
    assert {i.title: i.competitor_hits for i in items} == titles


def test_stock_analysis_hosts_are_blocked_for_competitors():
    for host in ("www.judal.co.kr", "kr.tradingview.com", "stockstory.org"):
        assert gn.is_blocked_competitor_host(host), host
    assert not gn.is_blocked_competitor_host("www.aitimes.com")
