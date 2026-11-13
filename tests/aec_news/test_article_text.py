"""aec-news 원문 본문 확인 테스트 (본문 추출, 인코딩, 이름 발췌)"""
import re

from src.aec_news.article_text import ArticleFetcher, decode_page, html_to_text, mention_excerpt


def test_html_to_text_uses_body_container_and_skips_noise():
    page = """<html><head><script>var 포비콘=1</script></head><body><nav>포비콘 메뉴</nav>
    <div class="article-head"><h1>파트너스 데이</h1></div>
    <article id="article-view-content-div" itemprop="articleBody">
      <p>아이디어정보기술은 페이컴스, 포비콘, 제호바 등 협력사가 발표했다.</p>
      <div class="ad-box">광고 포비콘</div><figure><img src=x><figcaption>사진 설명</figcaption></figure>
      <p>행사는 판교에서 열렸다.</p>
    </article>
    <div class="related-news">관련기사 인포시즈 서울대</div><footer>포비콘</footer></body></html>"""
    assert html_to_text(page) == "아이디어정보기술은 페이컴스, 포비콘, 제호바 등 협력사가 발표했다. 행사는 판교에서 열렸다."


def test_related_list_heading_skips_its_block_only():
    page = ("<div><p>메리츠화재 3분기 실적 발표 기사 본문입니다.</p>"
            "<div class='box'><h3>관련기사</h3><ul><li>라구나인베스트먼트, 올거나이즈 엑시트</li></ul></div>"
            "<p>끝 문단.</p></div>")
    text = html_to_text(page)
    assert "올거나이즈" not in text
    assert text == "메리츠화재 3분기 실적 발표 기사 본문입니다. 끝 문단."


def test_prose_containing_marker_words_is_not_cut():
    # 리뷰 지적: 문장 속 'more on', 'recommended', '관련 기사'에서 본문을 자르면 회사명을 놓친다
    page = ("<article itemprop='articleBody'><p>Contractors are leaning more on AI. The panel recommended "
            "Egnyte for files.</p><p>앞서 관련 기사에서 다룬 포비콘의 적산 AI가 소개됐다.</p></article>")
    text = html_to_text(page)
    assert "Egnyte" in text and "포비콘" in text


def test_heading_directly_in_body_container_ends_body():
    page = "<article itemprop='articleBody'><p>본문 인포시즈</p><strong>관련기사</strong><p>다른 기사 올거나이즈</p></article>"
    assert html_to_text(page) == "본문 인포시즈"


def test_unbalanced_tags_do_not_end_body_early():
    page = "<div class='article-body'><p>첫 문단<p>둘째 문단</span></b> 포비콘</div><p>바깥 문단</p>"
    assert html_to_text(page) == "첫 문단 둘째 문단 포비콘"


def test_body_inside_noise_named_wrapper_is_found():
    # 공감신문 실측: 본문(itemprop=articleBody)이 id='snsAnchor' 래퍼 안에 있다
    page = ("<section class='article-view-content'><div id='snsAnchor' class='sticky-article'><div class='article-body'>"
            "<article itemprop='articleBody'><p>플랜바이테크놀로지스가 엑스포에 참가한다.</p>"
            "<div class='ad-template'>광고</div></article></div></div></section>")
    assert html_to_text(page) == "플랜바이테크놀로지스가 엑스포에 참가한다."


def test_real_ndsoft_page_without_mention(tmp_path):
    # 뉴스톱 실측: 본문에 올거나이즈가 없고 관련기사 목록에만 있다 (오탐이던 사례)
    page = """<div class="article-body"><article id="article-view-content-div" class="article-veiw-body view-page"
      itemprop="articleBody"><p>메리츠화재의 올 3분기 누적 순이익이 1년전 대비 소폭 감소했다.</p></article></div>
      <section class="aside"><ul class="auto-article"><li>라구나인베스트먼트, 올거나이즈 엑시트</li></ul></section>"""
    assert "올거나이즈" not in html_to_text(page)


def test_html_to_text_without_article_tag_uses_body():
    assert html_to_text("<div><p>A &amp; B</p><p>인포시즈</p></div>") == "A & B 인포시즈"


def test_decode_page_meta_charset_euc_kr():
    raw = '<meta http-equiv="Content-Type" content="text/html; charset=euc-kr"><p>클라이원트 분석</p>'.encode("euc-kr")
    assert "클라이원트 분석" in decode_page(raw, "ISO-8859-1")
    assert decode_page("한국딥러닝".encode("utf-8"), None) == "한국딥러닝"


def test_mention_excerpt_window():
    text = "가" * 300 + " 클라이원트에 의뢰해 분석한 결과 " + "나" * 300
    ex = mention_excerpt(text, re.compile("클라이원트"), width=20)
    assert ex.startswith("…") and ex.endswith("…") and "클라이원트에 의뢰해" in ex
    assert mention_excerpt("이름 없음", re.compile("포비콘")) is None


class FakeResp:
    def __init__(self, status=200, ctype="text/html; charset=utf-8", body="<p>본문 포비콘</p>", error=None):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.encoding = "utf-8"
        self.body = body.encode("utf-8")
        self.error = error
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def iter_content(self, size):
        yield self.body[: len(self.body) // 2]
        if self.error:
            raise self.error
        yield self.body[len(self.body) // 2:]


class FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.headers = {}

    def get(self, url, timeout=None, stream=False):
        return self.resp


def test_fetcher_returns_none_for_errors_and_non_html():
    assert ArticleFetcher(FakeSession(FakeResp(status=403)), interval=0).fetch_text("u") is None
    assert ArticleFetcher(FakeSession(FakeResp(ctype="application/pdf")), interval=0).fetch_text("u") is None
    resp = FakeResp()
    assert ArticleFetcher(FakeSession(resp), interval=0).fetch_text("u") == "본문 포비콘"
    assert resp.closed


def test_fetcher_swallows_mid_body_errors():
    # 리뷰 지적(high): 본문을 읽다가 끊기면(urllib3 ProtocolError 등) 실행 전체가 멈췄다
    import urllib3
    for err in (urllib3.exceptions.ProtocolError("reset"), urllib3.exceptions.DecodeError("gzip"), OSError("x")):
        assert ArticleFetcher(FakeSession(FakeResp(error=err)), interval=0).fetch_text("u") is None


def test_fetcher_treats_bot_challenge_as_failure():
    challenge = "<html><body>Request unsuccessful. Incapsula incident ID: 1234</body></html>"
    assert ArticleFetcher(FakeSession(FakeResp(body=challenge)), interval=0).fetch_text("u") is None


def test_fetcher_stops_at_total_deadline():
    t = [0.0]

    def tick():
        t[0] += 20
        return t[0]

    class Slow(FakeResp):
        def iter_content(self, size):
            for _ in range(100):
                yield b"<p>chunk</p>"

    text = ArticleFetcher(FakeSession(Slow()), interval=0, monotonic=tick).fetch_text("u")
    assert text is not None and text.count("chunk") <= 3


def test_decode_page_cp949_extension_syllables():
    raw = '<meta charset="euc-kr"><p>똠방각하 포비콘</p>'.encode("cp949")   # '똠'은 EUC-KR 밖(CP949 확장)
    assert "똠방각하 포비콘" in decode_page(raw, None)
