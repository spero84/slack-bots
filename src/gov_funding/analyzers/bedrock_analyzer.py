"""Bedrock Claude 기반 관련성 분석"""
import json
from typing import TYPE_CHECKING, Optional

import boto3
from botocore.exceptions import ClientError

from ..storage import Announcement
from ..utils import get_config, logger
from ..utils.file_reader import extract_text_from_file

if TYPE_CHECKING:
    from ..crawlers.base_crawler import BaseCrawler


class BedrockAnalyzer:
    """Bedrock Claude를 이용한 공고 분석기"""

    def __init__(self, model_id: Optional[str] = None, region: Optional[str] = None):
        config = get_config()
        self.model_id = model_id or config.bedrock_model_id
        self.region = region or config.bedrock_region  # Bedrock은 us-west-2 사용
        self.client = boto3.client("bedrock-runtime", region_name=self.region)

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
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "temperature": 0.1,
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
            content = response["content"][0]["text"]
            # JSON 추출
            import re
            json_match = re.search(r"\{[^{}]+\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                score = float(data.get("relevance_score", 0.5))
                summary = data.get("summary")
                return score, summary
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"응답 파싱 실패: {e}")

        return 0.5, None


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
