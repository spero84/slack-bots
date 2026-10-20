"""Bedrock Claude 기반 관련성 분석

요청은 structured outputs(`output_config.format` json_schema)로 응답 형식을 강제한다.
그래도 비정상 응답(잘림, 거부, 스키마 미적용 모델)에 대비해 JSON 추출 → 정규식 필드 복구
순서의 폴백 파서를 유지한다.

실패 규약
- `BedrockAnalyzer.analyze_relevance()`는 API 오류 / 파싱 불가 / refusal이면 `(None, None)`을 반환한다.
  (예전 `(0.5, None)`은 임계값 0.7 미만이라 실패가 '관련 없음'으로 조용히 바뀌었다.)
- `filter_with_bedrock()`은 실패한 공고를 WARNING과 함께 제외한다 (저장되지 않으므로 다음 실행에서 재시도).
  처음 3건이 모두 실패하거나, 끝까지 실패가 3건 이상이면서 시도 건수의 50% 이상이면
  `BedrockUnavailableError`를 던진다.
"""
import json
import math
import re
from typing import TYPE_CHECKING, Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..storage import Announcement
from ..utils import get_config, logger
from ..utils.dates import calculate_d_day, now_kst, today_kst
from ..utils.deadline_text import parse_deadline_from_text
from ..utils.file_reader import detect_file_type, extract_text_from_file
from ..utils.text import clean_text

if TYPE_CHECKING:
    from ..crawlers.base_crawler import BaseCrawler


MAX_TOKENS = 1024
# 프롬프트에 넣는 상세 내용 상한. 2000자에서는 첨부 공고문 앞부분만 들어가 신청기간/자격이 잘렸다.
DETAIL_PROMPT_BUDGET = 6000
# 첨부 텍스트가 있을 때 페이지 본문이 차지할 수 있는 최대 길이 (나머지는 첨부 공고문)
PAGE_PROMPT_BUDGET = 2000
# 다운로드/추출을 시도할 첨부파일 수 (우선 파일이 실패하면 다음 후보 1개까지)
MAX_ATTACHMENT_ATTEMPTS = 2

# Bedrock 장애 판정
FAILURE_PROBE_COUNT = 3  # 처음 N건이 모두 실패하면 즉시 중단
FAILURE_MIN_COUNT = 3  # 최종 판정: 실패 건수 하한
FAILURE_RATIO = 0.5  # 최종 판정: 실패 비율 하한

# structured outputs 스키마 — minimum/maximum은 지원되지 않으므로 점수 범위는 코드에서 보정한다.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "relevance_score": {"type": "number"},
        "summary": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["relevance_score", "summary", "reason"],
}


class BedrockUnavailableError(RuntimeError):
    """Bedrock 분석이 대량으로 실패함 (인증 오류, 서비스 장애 등)

    filter_with_bedrock()이 던지며, 호출 측(main)은 이번 실행의 신규 판정을 보류하고
    result['errors']에 기록한다.
    """


def extract_json_object(content: str) -> Optional[dict]:
    """응답 텍스트에서 JSON 객체를 추출

    마크다운 코드펜스나 앞뒤 설명 문장이 붙어도 파싱된다.
    """
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    candidates = [text]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidates.append(match.group())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def normalize_score(value: Any) -> Optional[float]:
    """점수를 float으로 변환하고 [0, 1]로 보정 (변환 불가/NaN이면 None)"""
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(score):
        return None
    return min(max(score, 0.0), 1.0)


def normalize_summary(value: Any) -> Optional[str]:
    """요약문 정리: HTML 태그/엔티티 제거 후 공백·줄바꿈을 한 칸으로 합친다"""
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", clean_text(value)).strip()
    return text or None


_FALLBACK_SCORE = re.compile(r'"relevance_score"\s*:\s*"?(-?[0-9]*\.?[0-9]+)')
_FALLBACK_SUMMARY_START = re.compile(r'"summary"\s*:\s*"')
# summary 값 끝 ~ 다음 키 사이의 깨진 구분자: '",', '";', '";,', '","', 콤마 누락 등
_FALLBACK_SUMMARY_END = re.compile(
    r'"?\s*[,;]*\s*"?\s*\n?\s*"(?:reason|relevance_score)"\s*:'
)
# 다음 키가 없을 때: 닫는 따옴표 뒤 객체 끝/텍스트 끝
_FALLBACK_SUMMARY_TAIL = re.compile(r'"\s*[,;]?\s*(?:\}|$)')


def _unescape_json_string(body: str) -> str:
    """정규식으로 잘라낸 JSON 문자열 본문의 이스케이프(\\n, \\", \\uXXXX)를 해제"""
    for candidate in (body, re.sub(r'(?<!\\)"', r'\\"', body)):
        try:
            value = json.loads(f'"{candidate}"', strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(value, str):
            return value
    return body.replace('\\"', '"').replace("\\n", "\n")


def extract_fields_fallback(content: str) -> tuple[Optional[float], Optional[str]]:
    """JSON이 깨졌을 때 정규식으로 점수/요약만 복구

    Sonnet 5는 summary와 reason 사이 구분자를 깨뜨리거나('",' 대신 '";', '";,', '","', 콤마 누락)
    요약문 안에 이스케이프되지 않은 따옴표를 넣기도 한다. 요약은 'reason' 키 직전에서 끊어
    reason 텍스트가 요약에 섞이지 않게 하고, JSON 이스케이프를 해제한다. 점수는 [0, 1]로 보정한다.
    """
    score = None
    score_match = _FALLBACK_SCORE.search(content)
    if score_match:
        score = normalize_score(score_match.group(1))

    summary = None
    start = _FALLBACK_SUMMARY_START.search(content)
    if start:
        rest = content[start.end():]
        end = _FALLBACK_SUMMARY_END.search(rest)
        if end:
            body = rest[: end.start()]
        else:
            tail = _FALLBACK_SUMMARY_TAIL.search(rest)
            body = rest[: tail.start()] if tail else rest
        body = body.rstrip(' \n\t",;')
        summary = _unescape_json_string(body).strip() or None

    return score, summary


class BedrockAnalyzer:
    """Bedrock Claude를 이용한 공고 분석기"""

    def __init__(self, model_id: Optional[str] = None, region: Optional[str] = None):
        config = get_config()
        self.model_id = model_id or config.bedrock_model_id
        self.region = region or config.bedrock_region  # Bedrock은 us-west-2 사용
        # signature_version="v4" 명시 — AWS_BEARER_TOKEN_BEDROCK(Claude CLI용, 만료 가능)을
        # 무시하고 EC2 IAM Role(SigV4)로 인증한다.
        # adaptive 재시도 — ServiceUnavailable/Throttling 일시 장애에서 건별 실패를 줄인다.
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=self.region,
            config=Config(
                signature_version="v4",
                retries={"max_attempts": 6, "mode": "adaptive"},
            ),
        )

    async def analyze_relevance(
        self,
        announcement: Announcement,
        detail_content: Optional[str] = None,
    ) -> tuple[Optional[float], Optional[str]]:
        """공고 관련성 분석

        Args:
            announcement: 공고 정보
            detail_content: 상세 내용 (있는 경우)

        Returns:
            (관련성 점수 0-1, AI 요약). API 오류 / 파싱 불가 / refusal이면 (None, None).
            요약은 없을 수 있다 (max_tokens 잘림 등) — 점수만 있는 (score, None)도 성공이다.
        """
        prompt = self._build_prompt(announcement, detail_content)

        try:
            response = self._invoke_model(prompt)
        except ClientError as e:
            logger.error(f"Bedrock 호출 오류: {e}")
            return None, None
        except Exception as e:
            logger.error(f"Bedrock 호출 오류 ({type(e).__name__}): {e}")
            return None, None

        try:
            return self._parse_response(response)
        except Exception as e:
            logger.error(f"Bedrock 응답 처리 오류 ({type(e).__name__}): {e}")
            return None, None

    def _build_prompt(
        self,
        announcement: Announcement,
        detail_content: Optional[str] = None,
    ) -> str:
        """프롬프트 생성 (응답 형식은 structured outputs 스키마가 강제한다)"""
        fields = [
            f"공고 제목: {announcement.title}",
            f"분야: {announcement.category or '미분류'}",
            f"주관기관: {announcement.organization or '미상'}",
            f"소관부처: {announcement.department or '미상'}",
        ]
        if announcement.deadline:
            fields.append(f"마감일: {announcement.deadline:%Y-%m-%d}")
        content = "\n".join(fields)

        if detail_content:
            content += f"\n\n상세 내용:\n{detail_content[:DETAIL_PROMPT_BUDGET]}"

        today = today_kst()
        return f"""당신은 IT/AI 스타트업 지원사업 전문가입니다.
다음 정부 지원사업 공고가 IT, AI, 소프트웨어, 창업 관련 스타트업에 얼마나 관련되는지 평가해주세요.
오늘 날짜: {today:%Y-%m-%d}

{content}

relevance_score 평가 기준 (0.0-1.0, 1.0이 가장 관련성 높음):
- 1.0: IT/AI/SW 창업기업 직접 대상
- 0.8-0.9: IT/AI 관련 R&D, 기술개발 지원
- 0.6-0.7: 일반 창업/스타트업 지원 (IT 특화 아님)
- 0.4-0.5: 일부 관련 가능성 있음
- 0.0-0.3: 관련 없음 (농업, 제조업 등)

스타트업이 신청할 수 없는 공고는 분야와 관계없이 0.3 이하로 평가:
- 선정 결과·평가 결과 발표 (서류/발표평가 결과, 지원 대상 선정 공고 등)
- 단순 안내·공지, 법령·제도 개정 안내
- 이미 마감된 공고 (마감일이 오늘 이전)
단, 조달·용역 입찰, 주관기관·운영기관 모집, 해외 전시회 참가·해외 IT지원센터 입주 모집은
IT/AI 기업이 직접 신청하는 공고다. 창업기업 직접 지원이 아니거나 신청 대상이 기관·전문기업이라는
이유로 감점하지 말고, 사업 분야가 IT/AI/SW·창업 지원과 얼마나 관련되는지로 평가한다.

summary: 지원 대상, 지원 내용, 핵심 조건을 담은 2-3문장 일반 텍스트. HTML 태그/엔티티와 마크다운 금지.
reason: 점수 산정 이유 1문장.
"""

    def _invoke_model(self, prompt: str) -> dict:
        """Bedrock 모델 호출"""
        # Sonnet 5 / Opus 5는 temperature를 거부한다(ValidationException) — 보내지 않는다.
        # thinking을 명시적으로 끄면 짧은 JSON 응답만 반환되어 max_tokens 안에서 안전하다.
        # output_config.format(json_schema)으로 유효한 JSON 객체를 강제한다.
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
            "thinking": {"type": "disabled"},
            "output_config": {
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        response_body = json.loads(response["body"].read())
        return response_body

    def _parse_response(self, response: dict) -> tuple[Optional[float], Optional[str]]:
        """응답 파싱

        Returns:
            (점수, 요약). 거부/텍스트 없음/점수 복구 불가면 (None, None).
            stop_reason이 max_tokens면 점수는 유지하고 잘린 요약은 버린다.
        """
        stop_reason = response.get("stop_reason")
        if stop_reason == "refusal":
            logger.warning(f"Bedrock 응답 거부(refusal): {response.get('stop_details')}")
            return None, None

        # thinking 블록이 앞에 올 수 있으므로 첫 text 블록을 찾는다
        content = next(
            (
                b.get("text")
                for b in (response.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "text"
            ),
            None,
        )
        if not isinstance(content, str):
            logger.warning(f"응답에 text 블록이 없음 (stop_reason={stop_reason})")
            return None, None

        score: Optional[float] = None
        summary: Any = None
        data = extract_json_object(content)
        if data is not None:
            score = normalize_score(data.get("relevance_score"))
            summary = data.get("summary")

        if score is None:
            # JSON이 깨진 경우 필드 단위로 복구 시도
            score, summary = extract_fields_fallback(content)
            if score is None:
                logger.warning(f"응답 파싱 실패 (stop_reason={stop_reason}): {content[:300]}")
                return None, None
            logger.warning(
                f"JSON 파싱 실패 - 정규식으로 필드 복구 (stop_reason={stop_reason}): {content[:300]}"
            )

        if stop_reason == "max_tokens":
            usage = response.get("usage") or {}
            logger.warning(
                f"응답이 max_tokens({MAX_TOKENS})에서 잘림 "
                f"(output_tokens={usage.get('output_tokens')}) - 점수만 사용하고 요약은 버림"
            )
            summary = None

        return score, normalize_summary(summary)


def _clean_block(text: Optional[str]) -> str:
    """페이지/첨부 텍스트 정리 (줄 단위 clean_text)

    줄 단위로 처리해 첨부 공고문의 '< 유의 사항 >'처럼 꺾쇠가 여러 줄에 걸쳐 있을 때
    그 사이 본문 전체가 태그로 오인되어 지워지지 않게 한다.
    """
    if not text:
        return ""
    lines = [clean_text(line) for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _select_attachments(attachments: list) -> list[dict]:
    """텍스트 추출 대상 첨부파일 후보 (지원 형식만, '공고'가 들어간 파일 우선)

    크롤러는 대체로 공고문을 먼저 정렬하지만, 참가신청서 양식이 먼저 오는 경우도 있다.
    """
    supported = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        name = (att.get("name") or "").strip()
        url = (att.get("url") or "").strip()
        if not name or not url or detect_file_type(name) is None:
            continue
        supported.append({"name": name, "url": url})
    # 안정 정렬: '공고' 포함 파일 → 나머지 (각 그룹 내 원래 순서 유지)
    return sorted(supported, key=lambda a: 0 if "공고" in a["name"] else 1)


async def _extract_attachment_text(crawler: "BaseCrawler", attachments: list) -> str:
    """우선순위 첨부파일 1개의 텍스트 (실패/빈 텍스트면 다음 후보까지 시도)"""
    for att in _select_attachments(attachments)[:MAX_ATTACHMENT_ATTEMPTS]:
        name, url = att["name"], att["url"]
        logger.info(f"첨부파일 다운로드: {name}")
        file_bytes = await crawler.download_attachment(url)
        if not file_bytes:
            continue
        text = _clean_block(extract_text_from_file(file_bytes, name))
        if text:
            logger.info(f"텍스트 추출 완료: {name} ({len(text)}자)")
            return text
        logger.warning(f"첨부파일 텍스트 없음: {name}")
    return ""


async def _fetch_detail_parts(
    crawler: "BaseCrawler",
    announcement: Announcement,
) -> tuple[str, str]:
    """상세 페이지 본문과 첨부파일 텍스트를 각각 정리해서 반환

    Returns:
        (페이지 본문, 첨부파일 텍스트). 실패한 부분은 빈 문자열.
    """
    try:
        detail = await crawler.get_detail(announcement.id)
    except Exception as e:
        logger.warning(f"상세 내용 수집 실패 ({announcement.title}): {e}")
        return "", ""
    if not isinstance(detail, dict) or not detail:
        return "", ""

    content = detail.get("content")
    page_text = _clean_block(content) if isinstance(content, str) else ""

    attachment_text = ""
    try:
        attachment_text = await _extract_attachment_text(crawler, detail.get("attachments") or [])
    except Exception as e:
        logger.warning(f"첨부파일 처리 실패 ({announcement.title}): {e}")

    return page_text, attachment_text


async def _fetch_detail_content(
    crawler: "BaseCrawler",
    announcement: Announcement,
) -> Optional[str]:
    """크롤러를 통해 공고 상세 내용 및 첨부파일(HWP/HWPX/PDF/DOCX) 텍스트를 수집

    첨부파일은 지원 형식 중 이름에 '공고'가 들어간 파일을 우선하고, 없으면 첫 번째 지원 파일을 쓴다.
    파일명 끝의 크기 표기('공고문.hwpx [178.7 KB]')는 무시하고 확장자를 판별한다.

    Args:
        crawler: 해당 소스의 크롤러 인스턴스
        announcement: 공고 정보

    Returns:
        '페이지 본문\\n\\n첨부파일 텍스트' (HTML 정리됨) 또는 None
    """
    page_text, attachment_text = await _fetch_detail_parts(crawler, announcement)
    parts = [p for p in (page_text, attachment_text) if p]
    return "\n\n".join(parts) if parts else None


def _compose_prompt_detail(
    page_text: str,
    attachment_text: str,
    budget: int = DETAIL_PROMPT_BUDGET,
) -> Optional[str]:
    """프롬프트용 상세 내용 (예산 내에서 정보가 많은 순서로 배치)

    페이지 본문(공고 요약·메타 정보)을 먼저, 첨부 공고문을 뒤에 둔다. 첨부가 있으면 페이지 본문은
    PAGE_PROMPT_BUDGET까지만 써서 공고문 앞부분(개요·신청자격·신청기간)이 예산 안에 들어가게 한다.
    마감일은 _build_prompt()의 헤더 필드로 별도 표기된다.
    """
    if page_text and attachment_text:
        page = page_text[:PAGE_PROMPT_BUDGET]
        header = "\n\n[첨부 공고문]\n"
        remaining = max(budget - len(page) - len(header), 0)
        return f"{page}{header}{attachment_text[:remaining]}"
    text = page_text or attachment_text
    return text[:budget] if text else None


async def filter_with_bedrock(
    announcements: list[Announcement],
    threshold: float = 0.7,
    crawlers: Optional[dict[str, "BaseCrawler"]] = None,
) -> list[Announcement]:
    """Bedrock을 이용한 2차 필터링

    - crawlers에 해당 소스가 있으면 상세 페이지/첨부 공고문을 수집해 프롬프트에 넣는다.
    - 마감일이 없는 공고는 상세 텍스트에서 마감일을 추출해 deadline/d_day를 채운다.
      이미 마감된 공고는 Bedrock을 호출하지 않고 제외한다.
    - 분석에 실패한 공고(점수 None)는 WARNING 후 제외한다 (저장되지 않아 다음 실행에서 재시도).

    Args:
        announcements: 1차 필터링된 공고 목록
        threshold: 관련성 임계값
        crawlers: 소스별 크롤러 인스턴스 dict (상세 내용 수집용)

    Returns:
        필터링 및 점수 부여된 공고 목록

    Raises:
        BedrockUnavailableError: 처음 3건이 모두 실패했거나, 실패가 3건 이상이면서 시도의 50% 이상일 때
    """
    analyzer = BedrockAnalyzer()
    filtered: list[Announcement] = []
    attempted = 0
    failed = 0
    expired = 0
    today = today_kst()

    for ann in announcements:
        page_text, attachment_text = "", ""
        crawler = crawlers.get(ann.source.value) if crawlers else None
        if crawler is not None:
            page_text, attachment_text = await _fetch_detail_parts(crawler, ann)

        # 목록에 마감일이 없는 공고는 상세/첨부 텍스트에서 추출
        detail_text = "\n\n".join(p for p in (page_text, attachment_text) if p)
        if ann.deadline is None and detail_text:
            deadline = parse_deadline_from_text(detail_text, ref=now_kst())
            if deadline is not None:
                ann.deadline = deadline
                ann.d_day = calculate_d_day(deadline, today)
                if deadline.date() < today:
                    expired += 1
                    logger.info(f"제외 (마감): {ann.title} (마감 {deadline:%Y-%m-%d})")
                    continue
                logger.info(f"상세 내용에서 마감일 추출: {deadline:%Y-%m-%d} (D-{ann.d_day}) {ann.title}")

        attempted += 1
        score, summary = await analyzer.analyze_relevance(
            ann, _compose_prompt_detail(page_text, attachment_text)
        )

        if score is None:
            failed += 1
            logger.warning(f"Bedrock 분석 실패 - 이번 실행에서 제외 (다음 실행에서 재시도): {ann.title}")
            if attempted == FAILURE_PROBE_COUNT and failed == attempted:
                raise BedrockUnavailableError(f"Bedrock 분석 실패 {failed}/{attempted}건")
            continue

        ann.relevance_score = score
        ann.summary = summary

        if score >= threshold:
            filtered.append(ann)
            logger.info(f"통과 ({score:.2f}): {ann.title}")
        else:
            logger.info(f"제외 ({score:.2f}): {ann.title}")

    if failed >= FAILURE_MIN_COUNT and failed >= attempted * FAILURE_RATIO:
        raise BedrockUnavailableError(f"Bedrock 분석 실패 {failed}/{attempted}건")
    if failed:
        logger.warning(f"Bedrock 분석 실패 {failed}/{attempted}건 - 실패 공고는 다음 실행에서 재시도")

    logger.info(
        f"Bedrock 필터링: {len(announcements)}건 → {len(filtered)}건 "
        f"(분석 {attempted}건, 실패 {failed}건, 마감 제외 {expired}건)"
    )
    return filtered
