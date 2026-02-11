#!/usr/bin/env python3
"""AI 뉴스 모니터링 - Standalone 스크립트

APScheduler로 매일 8시에 실행됩니다.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from difflib import SequenceMatcher

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .analyzers.bedrock_summarizer import summarize_articles
from .crawlers import (
    AITimesCrawler,
    AnthropicCrawler,
    ArxivCrawler,
    AWSBlogCrawler,
    AzureBlogCrawler,
    DeepMindCrawler,
    ETNewsCrawler,
    GoogleBlogCrawler,
    GoogleResearchCrawler,
    HackerNewsCrawler,
    HuggingFaceCrawler,
    ITDailyCrawler,
    ITWorldCrawler,
    MediumCrawler,
    MSResearchCrawler,
    OpenAICrawler,
    TechCrunchCrawler,
)
from .notifiers.slack_notifier import send_ai_news_notification
from .storage.models import Article, NewsDigest
from .storage.vector_storage import AINewsVectorStorage
from .utils.config import get_config
from .utils.logger import logger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
scheduler_logger = logging.getLogger(__name__)


KOREAN_SOURCES = {"aitimes", "itworld", "etnews", "itdaily"}


def _is_similar(title1: str, title2: str, threshold: float = 0.6) -> bool:
    """두 정규화된 제목의 유사도가 임계값 이상인지 확인"""
    return SequenceMatcher(None, title1, title2).ratio() >= threshold


def deduplicate_articles(articles: list[Article]) -> list[Article]:
    """제목 기반 중복 제거

    1차: 정규화 제목 정확 일치 제거
    2차: 한국 뉴스 소스 간 유사 제목 제거 (SequenceMatcher 0.6 이상)
    """
    # 1차: 정확 일치 중복 제거
    seen: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        key = article.normalized_title
        if key not in seen:
            seen.add(key)
            unique.append(article)

    # 2차: 한국 소스 간 유사 제목 제거
    remove_indices: set[int] = set()
    korean_indices = [
        i for i, a in enumerate(unique)
        if a.source.value in KOREAN_SOURCES
    ]

    for idx_i, i in enumerate(korean_indices):
        if i in remove_indices:
            continue
        for j in korean_indices[idx_i + 1:]:
            if j in remove_indices:
                continue
            if _is_similar(unique[i].normalized_title, unique[j].normalized_title):
                logger.info(
                    f"유사 중복 제거: [{unique[j].source.value}] {unique[j].title[:40]}… "
                    f"← [{unique[i].source.value}] {unique[i].title[:40]}…"
                )
                remove_indices.add(j)

    if remove_indices:
        unique = [a for idx, a in enumerate(unique) if idx not in remove_indices]
        logger.info(f"한국 뉴스 유사 중복 제거: {len(remove_indices)}건")

    return unique


async def run_workflow() -> dict:
    """메인 워크플로우 실행

    1. 7개 소스에서 기사/논문 크롤링
    2. 중복 제거
    3. Bedrock 요약 및 중요도 평가
    4. S3 Vectors 신규 식별
    5. Slack 알림 전송
    """
    config = get_config()
    vector_storage = AINewsVectorStorage()
    vector_storage.ensure_index()

    result = {
        "timestamp": datetime.now().isoformat(),
        "crawled": {},
        "total_crawled": 0,
        "deduplicated": 0,
        "summarized": 0,
        "new_articles": 0,
        "notification": False,
        "errors": [],
    }

    # 1. 크롤링 (모든 소스)
    all_articles: list[Article] = []

    crawlers = [
        ArxivCrawler(),
        HackerNewsCrawler(),
        TechCrunchCrawler(),
        AnthropicCrawler(),
        OpenAICrawler(),
        DeepMindCrawler(),
        HuggingFaceCrawler(),
        AITimesCrawler(),
        ITWorldCrawler(),
        ETNewsCrawler(),
        ITDailyCrawler(),
        AWSBlogCrawler(),
        AzureBlogCrawler(),
        GoogleBlogCrawler(),
        MSResearchCrawler(),
        GoogleResearchCrawler(),
        MediumCrawler(),
    ]

    for crawler in crawlers:
        try:
            articles = await crawler.crawl(max_items=config.max_articles_per_source)
            result["crawled"][crawler.source.value] = len(articles)
            all_articles.extend(articles)
            logger.info(f"{crawler.name}: {len(articles)}건 수집")
        except Exception as e:
            logger.error(f"{crawler.name} 크롤링 오류: {e}")
            result["errors"].append(f"{crawler.name}: {str(e)}")
        finally:
            await crawler.close()

    result["total_crawled"] = len(all_articles)

    if not all_articles:
        logger.warning("크롤링 결과 없음")
        return result

    # 2. 중복 제거
    unique_articles = deduplicate_articles(all_articles)
    result["deduplicated"] = len(unique_articles)
    logger.info(f"중복 제거: {len(all_articles)}건 → {len(unique_articles)}건")

    # 3. S3 Vectors 신규 식별 (요약 전에 필터링하여 비용 절감)
    new_articles: list[Article] = []
    try:
        existing_keys = vector_storage.get_existing_keys()
        logger.info(f"기존 벡터 수: {len(existing_keys)}건")

        for article in unique_articles:
            if article.vector_key not in existing_keys:
                new_articles.append(article)

        logger.info(f"신규 기사: {len(new_articles)}건 (기존 {len(unique_articles) - len(new_articles)}건 스킵)")
    except Exception as e:
        logger.error(f"벡터 키 조회 오류: {e}")
        result["errors"].append(f"Vector key lookup: {str(e)}")
        new_articles = unique_articles  # 실패 시 전체를 신규로 처리

    if not new_articles:
        logger.info("신규 기사 없음 - 종료")
        return result

    # 4. Bedrock 요약 및 중요도 평가 (신규 기사만)
    try:
        summarized = await summarize_articles(
            new_articles,
            threshold=config.importance_threshold,
        )
        result["summarized"] = len(summarized)
    except Exception as e:
        logger.error(f"Bedrock 요약 오류: {e}")
        result["errors"].append(f"Bedrock summarization: {str(e)}")
        summarized = new_articles  # 실패 시 요약 없이 전송

    # 5. 벡터 저장
    try:
        vector_storage.upsert_articles(summarized)
    except Exception as e:
        logger.error(f"벡터 저장 오류: {e}")
        result["errors"].append(f"Vector storage: {str(e)}")

    result["new_articles"] = len(summarized)

    # 6. Slack 알림
    if summarized:
        digest = NewsDigest.from_articles(summarized)
        try:
            result["notification"] = await send_ai_news_notification(digest)
        except Exception as e:
            logger.error(f"Slack 알림 오류: {e}")
            result["errors"].append(f"Slack notification: {str(e)}")
    else:
        logger.info("중요 기사 없음 - 알림 스킵")

    return result


def execute_workflow():
    """동기 래퍼 - APScheduler에서 호출"""
    scheduler_logger.info("AI 뉴스 모니터링 시작...")
    try:
        result = asyncio.run(run_workflow())
        scheduler_logger.info(f"완료: {json.dumps(result, ensure_ascii=False)}")
    except Exception as e:
        scheduler_logger.error(f"워크플로우 오류: {e}")


def main():
    """메인 함수 - APScheduler로 스케줄 실행"""
    # --run-now 인자가 있으면 즉시 실행 후 종료
    if "--run-now" in sys.argv:
        scheduler_logger.info("즉시 실행 모드")
        execute_workflow()
        return

    scheduler = BlockingScheduler()

    # 월/수/금 8시 실행
    scheduler.add_job(
        execute_workflow,
        CronTrigger(day_of_week="mon,wed,fri", hour=8, minute=0, timezone="Asia/Seoul"),
        id="ai_news_monitor",
        name="AI News Monitor",
    )

    scheduler_logger.info("스케줄러 시작: 월/수/금 8시 AI 뉴스 모니터링")
    scheduler_logger.info("즉시 1회 테스트 실행...")
    execute_workflow()

    scheduler.start()


if __name__ == "__main__":
    main()
