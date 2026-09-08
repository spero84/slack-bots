"""Bedrock Claude 기반 관련성 분석"""
import json
import re
from typing import TYPE_CHECKING, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..storage import Announcement
from ..utils import get_config, logger
from ..utils.file_reader import extract_text_from_file

if TYPE_CHECKING:
    from ..crawlers.base_crawler import BaseCrawler


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


def extract_fields_fallback(content: str) -> tuple[Optional[float], Optional[str]]:
    """JSON이 깨졌을 때 정규식으로 점수/요약만 복구

    요약문 안에 이스케이프되지 않은 따옴표가 들어가거나 콤마가 누락되면
    json.loads가 실패한다. 이 경우 점수만이라도 살려서 0.5 폴백을 피한다.
    """
    score = None
    score_match = re.search(r'"relevance_score"\s*:\s*([0-9]*\.?[0-9]+)', content)
    if score_match:
        try:
            score = float(score_match.group(1))
        except ValueError:
            score = None

    summary = None
    summary_match = re.search(
        r'"summary"\s*:\s*"(.*?)"\s*(?:,\s*"reason"|,?\s*\}|$)', content, re.DOTALL
    )
    if summary_match:
        summary = summary_match.group(1).strip() or None

    return score, summary


class BedrockAnalyzer:
    """Bedrock Claude를 이용한 공고 분석기"""

    def __init__(self, model_id: Optional[str] = None, region: Optional[str] = None):
        config = get_config()
        self.model_id = model_id or config.bedrock_model_id
        self.region = region or config.bedrock_region  # Bedrock은 us-west-2 사용
        # signature_version="v4" 명시 — AWS_BEARER_TOKEN_BEDROCK(Claude CLI용, 만료 가능)을
        # 무시하고 EC2 IAM Role(SigV4)로 인증한다.
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=self.region,
            config=Config(signature_version="v4"),
        )

    async def analyze_relevance(
        self,
        announcement: Announcement,
        detail_content: Optional[str] = None,
    ) -> tuple[float, Optional[str]]:
        """공고 관련성 분석

        Args:
            announcement: 공고 정보
            detail_content: 상세 내용 (있는 경우)

        Returns:
            (관련성 점수 0-1, AI 요약)
        """
        prompt = self._build_prompt(announcement, detail_content)

        try:
            response = self._invoke_model(prompt)
            return self._parse_response(response)
        except ClientError as e:
            logger.error(f"Bedrock 호출 오류: {e}")
            return 0.5, None  # 기본값 반환
        except Exception as e:
            logger.error(f"분석 오류: {e}")
            return 0.5, None

    def _build_prompt(
        self,
        announcement: Announcement,
        detail_content: Optional[str] = None,
    ) -> str:
        """프롬프트 생성"""
        content = f"""
공고 제목: {announcement.title}
분야: {announcement.category or '미분류'}
주관기관: {announcement.organization or '미상'}
소관부처: {announcement.department or '미상'}
"""
        if detail_content:
            content += f"\n상세 내용:\n{detail_content[:2000]}"

        return f"""당신은 IT/AI 스타트업 지원사업 전문가입니다.
다음 정부 지원사업 공고가 IT, AI, 소프트웨어, 창업 관련 스타트업에 얼마나 관련되는지 평가해주세요.

{content}

다음 JSON 형식으로만 응답하세요:
{{
    "relevance_score": 0.0-1.0 사이 점수 (1.0이 가장 관련성 높음),
    "summary": "2-3문장 요약 (지원 대상, 지원 내용, 핵심 조건)",
    "reason": "점수 산정 이유 (1문장)"
}}

평가 기준:
- 1.0: IT/AI/SW 창업기업 직접 대상
- 0.8-0.9: IT/AI 관련 R&D, 기술개발 지원
- 0.6-0.7: 일반 창업/스타트업 지원 (IT 특화 아님)
- 0.4-0.5: 일부 관련 가능성 있음
- 0.0-0.3: 관련 없음 (농업, 제조업 등)
"""

    def _invoke_model(self, prompt: str) -> dict:
        """Bedrock 모델 호출"""
        # Sonnet 5 / Opus 5는 temperature를 거부한다(ValidationException).
        # thinking을 명시적으로 끄면 짧은 JSON 응답만 반환되어 max_tokens 안에서 안전하다.
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "thinking": {"type": "disabled"},
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

    def _parse_response(self, response: dict) -> tuple[float, Optional[str]]:
        """응답 파싱"""
        try:
            # thinking 블록이 앞에 올 수 있으므로 첫 text 블록을 찾는다
            content = next(
                b["text"] for b in response["content"] if b.get("type") == "text"
            )
        except (KeyError, StopIteration) as e:
            logger.warning(f"응답에 text 블록이 없음: {e}")
            return 0.5, None

        data = extract_json_object(content)
        if data is not None:
            try:
                return float(data.get("relevance_score", 0.5)), data.get("summary")
            except (TypeError, ValueError) as e:
                logger.warning(f"점수 변환 실패: {e}")

        # JSON이 깨진 경우 필드 단위로 복구 시도
        score, summary = extract_fields_fallback(content)
        if score is None and summary is None:
            logger.warning(f"응답 파싱 실패: {content[:300]}")
            return 0.5, None

        logger.warning("JSON 파싱 실패 - 정규식으로 필드 복구")
        return score if score is not None else 0.5, summary


async def _fetch_detail_content(
    crawler: "BaseCrawler",
    announcement: Announcement,
) -> Optional[str]:
    """크롤러를 통해 공고 상세 내용 및 첨부파일(HWP/HWPX/PDF/DOCX) 텍스트를 수집

    Args:
        crawler: 해당 소스의 크롤러 인스턴스
        announcement: 공고 정보

    Returns:
        상세 내용 텍스트 또는 None
    """
    try:
        detail = await crawler.get_detail(announcement.id)
        if not detail:
            return None

        parts = []

        # 페이지 본문 텍스트
        content = detail.get("content", "")
        if content:
            parts.append(content)

        # 첨부파일 텍스트 추출 (첫 번째 지원 파일만)
        attachments = detail.get("attachments", [])
        for att in attachments:
            name = att.get("name", "")
            url = att.get("url", "")
            if not name or not url:
                continue

            ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
            if ext not in ("hwp", "hwpx", "pdf", "docx"):
                continue

            logger.info(f"첨부파일 다운로드: {name}")
            file_bytes = await crawler.download_attachment(url)
            if file_bytes:
                file_text = extract_text_from_file(file_bytes, name)
                if file_text:
                    parts.append(file_text)
                    logger.info(f"텍스트 추출 완료: {name} ({len(file_text)}자)")
            break  # 첫 번째 지원 파일만 처리

        return "\n\n".join(parts) if parts else None

    except Exception as e:
        logger.warning(f"상세 내용 수집 실패 ({announcement.title}): {e}")
        return None


async def filter_with_bedrock(
    announcements: list[Announcement],
    threshold: float = 0.7,
    crawlers: Optional[dict[str, "BaseCrawler"]] = None,
) -> list[Announcement]:
    """Bedrock을 이용한 2차 필터링

    Args:
        announcements: 1차 필터링된 공고 목록
        threshold: 관련성 임계값
        crawlers: 소스별 크롤러 인스턴스 dict (상세 내용 수집용)

    Returns:
        필터링 및 점수 부여된 공고 목록
    """
    analyzer = BedrockAnalyzer()
    filtered = []

    for ann in announcements:
        # 크롤러가 제공된 소스는 상세 내용 수집
        detail_content = None
        if crawlers and ann.source.value in crawlers:
            detail_content = await _fetch_detail_content(
                crawlers[ann.source.value], ann
            )

        score, summary = await analyzer.analyze_relevance(ann, detail_content)
        ann.relevance_score = score
        ann.summary = summary

        if score >= threshold:
            filtered.append(ann)
            logger.info(f"통과 ({score:.2f}): {ann.title}")
        else:
            logger.debug(f"제외 ({score:.2f}): {ann.title}")

    logger.info(f"Bedrock 필터링: {len(announcements)}건 → {len(filtered)}건")
    return filtered
