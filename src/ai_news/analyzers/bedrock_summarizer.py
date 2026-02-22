"""Bedrock Claude 기반 AI 뉴스 요약 및 중요도 평가"""
import json
import re
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ..storage.models import Article
from ..utils.config import get_config
from ..utils.logger import logger


class BedrockSummarizer:
    """Bedrock Claude를 이용한 기사 요약 및 중요도 평가"""

    def __init__(self, model_id: Optional[str] = None, region: Optional[str] = None):
        config = get_config()
        self.model_id = model_id or config.bedrock_model_id
        self.region = region or config.bedrock_region
        self.client = boto3.client("bedrock-runtime", region_name=self.region)

    async def summarize_and_score(self, article: Article) -> tuple[float, Optional[str]]:
        """기사 요약 및 중요도 평가

        Returns:
            (중요도 점수 0-1, 한국어 요약)
        """
        prompt = self._build_prompt(article)

        try:
            response = self._invoke_model(prompt)
            return self._parse_response(response)
        except ClientError as e:
            logger.error(f"Bedrock 호출 오류: {e}")
            return 0.5, None
        except Exception as e:
            logger.error(f"요약 오류: {e}")
            return 0.5, None

    def _build_prompt(self, article: Article) -> str:
        """프롬프트 생성"""
        content = f"제목: {article.title}\n"
        content += f"출처: {article.source.value}\n"
        content += f"카테고리: {article.category.value}\n"

        if article.authors:
            content += f"저자: {', '.join(article.authors[:5])}\n"
        if article.tags:
            content += f"태그: {', '.join(article.tags[:10])}\n"
        if article.summary:
            content += f"\n원문 요약/초록:\n{article.summary[:2000]}\n"

        return f"""당신은 AI/ML 분야 전문 기술 뉴스 편집자입니다.
다음 기사/논문의 중요도를 평가하고 한국어로 요약해주세요.

[회사 컨텍스트]
우리 회사는 문서 기반 검색을 통해 AI를 활용하는 회사입니다.
핵심 기술 분야: LLM, RAG, OCR, Vector 검색, 문서 처리, 임베딩, 자연어처리, 시맨틱 검색

{content}

다음 JSON 형식으로만 응답하세요:
{{
    "importance_score": 0.0-1.0 사이 점수,
    "ai_summary": "한국어 3-5문장 요약. 반드시 포함: 1) 핵심 내용 요약 2) 이것이 중요한 이유 3) 문서 기반 검색/AI 활용 기업 관점에서 알아야 할 이유와 시사점",
    "reason": "점수 산정 이유 (1문장)"
}}

평가 기준:
- 1.0: 획기적 신모델/아키텍처 발표, 주요 벤치마크 경신, 업계 판도 변화
- 0.8-0.9: 주요 회사 제품 업데이트, 중요 연구 결과, 새로운 기술 돌파구
- 0.6-0.7: 일반적 AI 뉴스, 보통 수준의 논문, 기능 업데이트
- 0.4-0.5: 마이너 업데이트, 간접적 관련성
- 0.0-0.3: AI/ML과 관련성 낮음
- 우리 회사에 직접 관련(RAG, 벡터 검색, 문서 OCR, 임베딩, 시맨틱 검색 등): +0.2 가산"""

    def _invoke_model(self, prompt: str) -> dict:
        """Bedrock 모델 호출"""
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
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

        return json.loads(response["body"].read())

    def _parse_response(self, response: dict) -> tuple[float, Optional[str]]:
        """응답 파싱"""
        try:
            content = response["content"][0]["text"]
            json_match = re.search(r"\{[^{}]+\}", content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                score = float(data.get("importance_score", 0.5))
                summary = data.get("ai_summary")
                return score, summary
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"응답 파싱 실패: {e}")

        return 0.5, None


async def summarize_articles(
    articles: list[Article],
    threshold: float = 0.5,
) -> list[Article]:
    """기사 목록 요약 및 필터링

    Args:
        articles: 기사 목록
        threshold: 중요도 임계값

    Returns:
        요약 및 필터링된 기사 목록
    """
    summarizer = BedrockSummarizer()
    filtered = []

    for article in articles:
        score, ai_summary = await summarizer.summarize_and_score(article)
        article.importance_score = score
        article.ai_summary = ai_summary

        if score >= threshold:
            filtered.append(article)
            logger.info(f"통과 ({score:.2f}): [{article.source.value}] {article.title}")
        else:
            logger.debug(f"제외 ({score:.2f}): [{article.source.value}] {article.title}")

    logger.info(f"Bedrock 요약: {len(articles)}건 → {len(filtered)}건 (threshold={threshold})")
    return filtered
