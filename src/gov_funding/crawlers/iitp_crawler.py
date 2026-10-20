"""IITP(정보통신기획평가원) 크롤러 - requests 기반 (IRIS 사업공고)

www.iitp.kr robots.txt가 'Disallow: /'이므로 IITP 사이트 대신 범부처통합연구지원시스템(IRIS)에서
전문기관=정보통신기획평가원(sorgnId=10004)인 사업공고를 수집한다 (IRIS robots.txt는 공고 목록 허용).

목록: POST https://www.iris.go.kr/contents/retrieveBsnsAncmBtinSituList.do (form, JSON 응답)
- ancmPrg: ancmIng(접수중) / ancmPre(접수예정) / ancmEnd(마감) — 접수중·접수예정만 수집
- 페이지당 10건 고정, pageIndex로 페이지 이동 (paginationInfo.totalPageCount)
- 응답 listBsnsAncmBtinSitu[]: ancmId, ancmTl, ancmDe('YYYY-MM-DD'), rcveStrDe/rcveEndDe('YYYY.MM.DD' 또는 null), sorgnNm ...
- 접수예정 목록에는 접수기간이 없는 오래된 공고(2018~2025)도 섞여 있다 → deadline_filter가 공고일(posted_date)로 제외

상세: GET https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId={id} (서버사이드 렌더링, 쿠키 불필요)
- ancmPrg 파라미터는 내용에 영향 없음
- 첨부: a.file_down의 f_bsnsAncm_downloadAtchFile('atchDocId','atchFileId','파일명',...)
  → GET /comm/file/fileDownload.do?atchDocId=...&atchFileId=... (쿠키 없이 다운로드 가능)
"""
import asyncio
import re
from datetime import date, datetime
from typing import Optional, Sequence
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from ..storage import Announcement, Source
from ..utils import CRAWL_SOURCES, logger
from ..utils.dates import calculate_d_day, today_kst
from .base_crawler import BaseCrawler

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# ancmPrg → 분류 라벨
PRG_LABELS = {"ancmIng": "접수중", "ancmPre": "접수예정", "ancmEnd": "마감"}

DEFAULT_DEPARTMENT = "과학기술정보통신부"
DEFAULT_ORGANIZATION = "정보통신기획평가원"

# 2026.11.16 / 2026-11-16
DATE_PATTERN = re.compile(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})")

# f_bsnsAncm_downloadAtchFile('atchDocId','atchFileId','파일명' ,'크기')
DOWNLOAD_JS_PATTERN = re.compile(r"f_bsnsAncm_downloadAtchFile\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']*)'")


_BLOCK_TAGS = ["p", "div", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd"]


def _element_text(el) -> str:
    """요소 텍스트 추출 (블록 요소 단위 줄바꿈, 스크립트 제외)

    에디터 본문은 한 문단이 여러 <span>으로 쪼개져 있어 get_text("\\n")을 쓰면 단어 중간에서 줄이 깨진다.
    """
    for tag in el.select("script, style"):
        tag.decompose()
    for br in el.find_all("br"):
        br.replace_with("\n")
    for tag in el.find_all(["td", "th"]):
        tag.append(" ")
    for tag in el.find_all(_BLOCK_TAGS):
        tag.append("\n")
    lines = (re.sub(r"[ \t\u00a0\u3000]+", " ", line).strip() for line in el.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def parse_date(text: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    """IRIS 날짜 문자열 파싱 ('2026.11.16' / '2026-10-15')

    end_of_day=True면 23:59:59로 설정 (마감일용).
    """
    if not text:
        return None
    match = DATE_PATTERN.search(str(text))
    if not match:
        return None
    try:
        year, month, day = map(int, match.groups())
        if end_of_day:
            return datetime(year, month, day, 23, 59, 59)
        return datetime(year, month, day)
    except ValueError:
        return None


def _attachment_priority(name: str) -> int:
    """첨부파일 정렬 우선순위 — 공고문이 먼저 (Bedrock 분석은 첫 번째 지원 파일만 사용)"""
    if "공고문" in name:
        return 0
    if "공고" in name:
        return 1
    return 2


class IitpListError(Exception):
    """IRIS 목록 조회 실패 (재시도 소진 또는 비정상 응답)"""


class IitpUnavailableError(IitpListError):
    """IRIS 접속 불가 (ConnectionError/Timeout/5xx 재시도 소진) — 남은 상태 목록도 요청하지 않는다"""


class IitpCrawler(BaseCrawler):
    """IITP 크롤러 (IRIS 사업공고 JSON)"""

    source = Source.IITP
    name = "IITP"

    def __init__(
        self,
        retry_delays: Optional[Sequence[float]] = None,
        request_delay: float = 1.0,
        timeout: float = 30,
    ):
        """
        Args:
            retry_delays: 목록 요청 재시도 전 대기 시간(초) 목록. 길이+1이 최대 시도 횟수 (기본 [10, 60] → 3회)
            request_delay: 목록 페이지/상태 간 요청 간격(초)
            timeout: 요청 타임아웃(초)
        """
        self.config = CRAWL_SOURCES["iitp"]
        self.retry_delays = tuple(retry_delays if retry_delays is not None else self.config["retry_delays"])
        self.request_delay = request_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })
        # 상세 URL 생성용: 공고 ID → 수집 당시 ancmPrg
        self._prg_by_id: dict[str, str] = {}

    async def crawl(self, max_items: int = 50) -> list[Announcement]:
        """IRIS에서 IITP 접수중·접수예정 공고 수집

        접수중 → 접수예정 순서, 각 목록은 IRIS 정렬(공고일 최신순)을 유지한다.
        - 상태(ancmPrg)별로 오류를 격리한다: 한 상태 목록이 실패해도(4xx, 비정상 응답, 예상치 못한 오류)
          그때까지 수집한 공고는 유지하고 다음 상태를 계속 수집한다.
        - 재시도 후에도 접속 불가(IitpUnavailableError)면 사이트 장애로 보고 남은 상태는 요청하지 않는다.
        - 형식이 이상한 항목 하나는 WARNING을 남기고 건너뛴다 (crawl() 전체가 실패하지 않도록).
        """
        today = today_kst()
        announcements: list[Announcement] = []
        seen_ids: set[str] = set()
        max_pages = self.config.get("max_pages", 5)
        first_request = True

        for prg in self.config["ancm_prgs"]:
            if len(announcements) >= max_items:
                break
            label = PRG_LABELS.get(prg, prg)
            page = 1
            try:
                while page <= max_pages and len(announcements) < max_items:
                    if not first_request and self.request_delay:
                        await asyncio.sleep(self.request_delay)
                    first_request = False

                    data = await self._fetch_list(prg, page)
                    rows = data.get("listBsnsAncmBtinSitu")
                    if not isinstance(rows, list):
                        raise IitpListError(f"응답에 listBsnsAncmBtinSitu 없음 (ancmPrg={prg}, 키={list(data)[:10]})")

                    added = 0
                    for row in rows:
                        try:
                            ann = self._parse_row(row, prg, today)
                        except Exception as e:
                            row_id = row.get("ancmId") if isinstance(row, dict) else None
                            logger.warning(
                                f"IITP(IRIS) {label} 항목 파싱 오류 - 건너뜀 (ancmId={row_id}): {type(e).__name__}: {e}"
                            )
                            continue
                        if ann is None or ann.id in seen_ids:
                            continue
                        seen_ids.add(ann.id)
                        self._prg_by_id[ann.id] = prg
                        announcements.append(ann)
                        added += 1

                    total_pages = self._total_pages(data)
                    logger.info(
                        f"IITP(IRIS) {label} 페이지 {page}/{total_pages}: "
                        f"{len(rows)}건 중 {added}건 추가 (누적 {len(announcements)}건)"
                    )
                    if not rows or page >= total_pages:
                        break
                    page += 1
            except IitpUnavailableError as e:
                logger.warning(f"IITP(IRIS) 목록 조회 실패 - 사이트 장애로 보고 수집 중단 (누적 {len(announcements)}건 유지): {e}")
                break
            except IitpListError as e:
                logger.warning(
                    f"IITP(IRIS) {label} 목록 조회 실패 (page={page}) - {label} 수집 중단, 다음 상태 계속: {e}"
                )
            except Exception as e:
                logger.warning(
                    f"IITP(IRIS) {label} 목록 처리 오류 (page={page}) - {label} 수집 중단, 다음 상태 계속: "
                    f"{type(e).__name__}: {e}"
                )

        result = announcements[:max_items]
        if not result:
            logger.warning("IITP 크롤링 결과 0건 - IRIS 사업공고 API/사이트 점검 필요")
        else:
            logger.info(f"IITP 크롤링 완료: 총 {len(result)}건")
        return result

    async def _fetch_list(self, prg: str, page: int) -> dict:
        """목록 API 호출 (ConnectionError/Timeout/HTTP 5xx는 재시도)

        Raises:
            IitpUnavailableError: 재시도 소진 (접속 불가)
            IitpListError: 4xx, JSON 파싱 실패, 비정상 응답
        """
        form = {
            "ancmPrg": prg,
            "pageIndex": str(page),
            "sorgnId": self.config["sorgn_id"],
            "sorgnIdArr": self.config["sorgn_id"],
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.config["list_url"],
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        attempts = len(self.retry_delays) + 1

        for attempt in range(1, attempts + 1):
            reason = None
            try:
                response = self.session.post(
                    self.config["list_api_url"], data=form, headers=headers, timeout=self.timeout
                )
                if response.status_code >= 500:
                    reason = f"HTTP {response.status_code}"
                else:
                    response.raise_for_status()
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                reason = f"{type(e).__name__}: {e}"
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                if status is not None and status >= 500:
                    reason = f"HTTP {status}"
                else:
                    raise IitpListError(f"HTTP {status} (ancmPrg={prg}, page={page})") from e
            except requests.exceptions.RequestException as e:
                raise IitpListError(f"{type(e).__name__}: {e}") from e

            if reason is None:
                try:
                    data = response.json()
                except ValueError as e:
                    raise IitpListError(
                        f"JSON 파싱 실패 ({type(e).__name__}, HTTP {response.status_code}, "
                        f"content-type={response.headers.get('content-type')})"
                    ) from e
                if not isinstance(data, dict):
                    raise IitpListError(f"예상치 못한 응답 형식: {type(data).__name__}")
                return data

            if attempt < attempts:
                delay = self.retry_delays[attempt - 1]
                logger.warning(
                    f"IITP(IRIS) 목록 요청 실패 ({attempt}/{attempts}, ancmPrg={prg}, page={page}): "
                    f"{reason} - {delay}초 후 재시도"
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    f"IITP(IRIS) 목록 요청 실패 ({attempt}/{attempts}, ancmPrg={prg}, page={page}): {reason}"
                )

        raise IitpUnavailableError(f"{attempts}회 시도 모두 실패 (ancmPrg={prg}, page={page})")

    @staticmethod
    def _total_pages(data: dict) -> int:
        """paginationInfo.totalPageCount (없으면 1)"""
        info = data.get("paginationInfo") or {}
        try:
            return max(int(info.get("totalPageCount") or 1), 1)
        except (TypeError, ValueError):
            return 1

    def _parse_row(self, row: dict, prg: str, today: Optional[date] = None) -> Optional[Announcement]:
        """목록 JSON 항목 → Announcement"""
        if not isinstance(row, dict):
            return None
        ancm_id = str(row.get("ancmId") or "").strip()
        title = (row.get("ancmTl") or "").strip()
        if not ancm_id or not title:
            logger.debug(f"IITP(IRIS) ID/제목 없는 항목 스킵: {row}")
            return None

        deadline = parse_date(row.get("rcveEndDe"), end_of_day=True)
        label = PRG_LABELS.get(prg, prg)

        return Announcement(
            id=ancm_id,
            source=self.source,
            title=title,
            category=f"사업공고({label})",
            deadline=deadline,
            d_day=calculate_d_day(deadline, today or today_kst()),
            department=(row.get("blngGovdSeNm") or "").strip() or DEFAULT_DEPARTMENT,
            organization=(row.get("sorgnNm") or "").strip() or DEFAULT_ORGANIZATION,
            url=self.config["detail_url_template"].format(id=ancm_id, prg=prg),
            posted_date=parse_date(row.get("ancmDe")),
        )

    async def get_detail(self, announcement_id: str) -> Optional[dict]:
        """IRIS 공고 상세 (본문 텍스트 + 첨부파일)

        Returns:
            {'content': str, 'attachments': [{'name': str, 'url': str}]} — 공고문 첨부가 먼저. 실패 시 None
        """
        params = {"ancmId": announcement_id}
        prg = self._prg_by_id.get(announcement_id)
        if prg:
            params["ancmPrg"] = prg

        try:
            response = self.session.get(
                self.config["detail_page_url"],
                params=params,
                headers={"Referer": self.config["list_url"]},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            logger.warning(f"IITP(IRIS) 상세 조회 실패 ({announcement_id}): HTTP {status}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"IITP(IRIS) 상세 조회 실패 ({announcement_id}): {type(e).__name__}: {e}")
            return None

        detail = self.parse_detail_page(response.text)
        if detail is None:
            logger.warning(f"IITP(IRIS) 상세 페이지 구조 확인 필요 ({announcement_id}): 공고 본문 영역 없음")
        return detail

    def parse_detail_page(self, html: str) -> Optional[dict]:
        """상세 페이지 HTML 파싱 — 공고 영역(.tstyle_view)이 없으면 None"""
        soup = BeautifulSoup(html, "lxml")
        view = soup.select_one(".tstyle_view")
        if view is None:
            return None

        parts = []
        # 메타 정보 (소관부처, 전문기관, 공고번호, 공고명, 공고일자, 접수기간 ...)
        # 사업담당자 연락처(이름·전화번호)는 관련성 판단에 불필요하므로 제외
        for li in view.select(".title_area ul.list_dot > li"):
            label = li.find("strong")
            if label is not None and "연락처" in label.get_text():
                continue
            text = re.sub(r"\s+", " ", li.get_text(" ", strip=True))
            if text:
                parts.append(text)

        body = view.select_one(".tb_contents .se-contents") or view.select_one(".tb_contents")
        if body is not None:
            body_text = _element_text(body)
            if body_text:
                parts.append(body_text)

        return {
            "content": "\n".join(parts)[:5000],
            "attachments": self._parse_attachments(view),
        }

    def _parse_attachments(self, view) -> list[dict]:
        """첨부파일 목록 (공고문 우선 정렬, 파일명은 실제 확장자로 끝남)"""
        attachments = []
        seen = set()
        for link in view.select("a.file_down"):
            match = DOWNLOAD_JS_PATTERN.search(link.get("href", "") + " " + link.get("onclick", ""))
            if not match:
                continue
            doc_id, file_id, js_name = match.groups()
            name_elem = link.select_one(".text")
            name = (name_elem.get_text(strip=True) if name_elem else "") or js_name.strip()
            if not name or (doc_id, file_id) in seen:
                continue
            seen.add((doc_id, file_id))
            url = f"{self.config['file_download_url']}?{urlencode({'atchDocId': doc_id, 'atchFileId': file_id})}"
            attachments.append({"name": name, "url": url})

        attachments.sort(key=lambda a: _attachment_priority(a["name"]))
        return attachments

    async def download_attachment(self, url: str, timeout: int = 30) -> Optional[bytes]:
        """첨부파일 다운로드 (세션 헤더 사용)"""
        try:
            response = self.session.get(
                url, headers={"Referer": self.config["detail_page_url"]}, timeout=timeout
            )
            response.raise_for_status()
            return response.content
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            logger.warning(f"IITP(IRIS) 첨부파일 다운로드 실패 ({url}): HTTP {status}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"IITP(IRIS) 첨부파일 다운로드 실패 ({url}): {type(e).__name__}: {e}")
        return None

    async def close(self):
        """세션 종료"""
        self.session.close()
