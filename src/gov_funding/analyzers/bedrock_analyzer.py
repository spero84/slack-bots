"""Bedrock Claude 기반 관련성 분석"""
import json
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ..storage import Announcement
from ..utils import get_config, logger


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


async def filter_with_bedrock(
    announcements: list[Announcement],
    threshold: float = 0.7,
) -> list[Announcement]:
    """Bedrock을 이용한 2차 필터링

    Args:
        announcements: 1차 필터링된 공고 목록
        threshold: 관련성 임계값

    Returns:
        필터링 및 점수 부여된 공고 목록
    """
    analyzer = BedrockAnalyzer()
    filtered = []

    for ann in announcements:
        score, summary = await analyzer.analyze_relevance(ann)
        ann.relevance_score = score
        ann.summary = summary

        if score >= threshold:
            filtered.append(ann)
            logger.info(f"통과 ({score:.2f}): {ann.title}")
        else:
            logger.debug(f"제외 ({score:.2f}): {ann.title}")

    logger.info(f"Bedrock 필터링: {len(announcements)}건 → {len(filtered)}건")
    return filtered
