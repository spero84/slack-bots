#!/usr/bin/env python3
"""정부 지원사업 공고 모니터링 - Standalone 스크립트

APScheduler로 매일 9시에 실행됩니다.
"""

import asyncio
import json
import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .analyzers import filter_with_bedrock, keyword_filter
from .crawlers import BizinfoCrawler, NipaCrawler
from .notifiers import send_gmail_notification, send_slack_notification
from .storage import NotificationPayload, S3Storage, Source, deduplicate_announcements
from .utils import get_config, logger

# Playwright is optional
try:
    from .crawlers import KStartupCrawler
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not available - K-Startup crawling disabled")

# 스케줄러 로깅
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
scheduler_logger = logging.getLogger(__name__)


async def run_workflow() -> dict:
    """메인 워크플로우 실행

    1. K-Startup, 기업마당에서 공고 크롤링
    2. 중복 제거 및 병합
    3. 키워드 + Bedrock 필터링
    4. S3 스냅샷 비교로 신규/마감임박 식별
    5. Slack/Gmail 알림 전송

    Returns:
        실행 결과 요약
    """
    config = get_config()
    storage = S3Storage()

    result = {
        "timestamp": datetime.now().isoformat(),
        "crawled": {"kstartup": 0, "bizinfo": 0, "nipa": 0},
        "filtered": 0,
        "new_announcements": 0,
        "deadline_soon": 0,
        "notifications": {"slack": False, "gmail": False},
        "errors": [],
    }

    # 1. 크롤링
    kstartup_announcements = []
    bizinfo_announcements = []
    nipa_announcements = []

    bizinfo_crawler = BizinfoCrawler()
    nipa_crawler = NipaCrawler()
    kstartup_crawler = KStartupCrawler() if PLAYWRIGHT_AVAILABLE else None

    try:
        # Bizinfo 크롤링 (항상 실행)
        bizinfo_announcements = await bizinfo_crawler.crawl(max_items=50)
        result["crawled"]["bizinfo"] = len(bizinfo_announcements)

        # NIPA 크롤링 (항상 실행)
        nipa_announcements = await nipa_crawler.crawl(max_items=50)
        result["crawled"]["nipa"] = len(nipa_announcements)

        # K-Startup 크롤링 (Playwright 사용 가능한 경우만)
        if kstartup_crawler:
            kstartup_announcements = await kstartup_crawler.crawl(max_items=50)
            result["crawled"]["kstartup"] = len(kstartup_announcements)
        else:
            logger.info("K-Startup 크롤링 스킵 (Playwright 미설치)")

        logger.info(
            f"크롤링 완료 - K-Startup: {len(kstartup_announcements)}건, "
            f"Bizinfo: {len(bizinfo_announcements)}건, "
            f"NIPA: {len(nipa_announcements)}건"
        )

    except Exception as e:
        logger.error(f"크롤링 오류: {e}")
        result["errors"].append(f"Crawling error: {str(e)}")
    finally:
        await bizinfo_crawler.close()
        await nipa_crawler.close()
        if kstartup_crawler:
            await kstartup_crawler.close()

    if not kstartup_announcements and not bizinfo_announcements and not nipa_announcements:
        logger.warning("크롤링 결과 없음")
        return result

    # 2. 중복 제거 및 병합
    all_announcements = deduplicate_announcements(
        kstartup_announcements,
        bizinfo_announcements,
        nipa_announcements,
    )

    # 3. 키워드 필터링 (1차)
    keyword_filtered = keyword_filter(all_announcements)

    # 4. Bedrock 필터링 (2차)
    try:
        final_filtered = await filter_with_bedrock(
            keyword_filtered,
            threshold=config.relevance_threshold,
        )
        result["filtered"] = len(final_filtered)
    except Exception as e:
        logger.error(f"Bedrock 필터링 오류: {e}")
        result["errors"].append(f"Bedrock filtering error: {str(e)}")
        final_filtered = keyword_filtered  # Bedrock 실패 시 키워드 필터 결과 사용

    # 5. 스냅샷 비교 (출처별)
    kstartup_filtered = [a for a in final_filtered if a.source == Source.KSTARTUP]
    bizinfo_filtered = [a for a in final_filtered if a.source == Source.BIZINFO]
    nipa_filtered = [a for a in final_filtered if a.source == Source.NIPA]

    combined_payload = NotificationPayload()

    for source, filtered_list in [
        (Source.KSTARTUP, kstartup_filtered),
        (Source.BIZINFO, bizinfo_filtered),
        (Source.NIPA, nipa_filtered),
    ]:
        if not filtered_list:
            continue

        new_snapshot, payload = storage.compare_snapshots(
            filtered_list,
            source,
            deadline_alert_days=config.deadline_alert_days,
        )

        # 스냅샷 저장
        storage.save_snapshot(new_snapshot)

        # 알림 페이로드 병합
        combined_payload.new_announcements.extend(payload.new_announcements)
        combined_payload.deadline_soon.extend(payload.deadline_soon)

    result["new_announcements"] = len(combined_payload.new_announcements)
    result["deadline_soon"] = len(combined_payload.deadline_soon)

    # 6. 알림 전송
    if combined_payload.has_content:
        # Slack 알림
        try:
            result["notifications"]["slack"] = await send_slack_notification(combined_payload)
        except Exception as e:
            logger.error(f"Slack 알림 오류: {e}")
            result["errors"].append(f"Slack notification error: {str(e)}")

        # Gmail 알림
        try:
            result["notifications"]["gmail"] = await send_gmail_notification(combined_payload)
        except Exception as e:
            logger.error(f"Gmail 알림 오류: {e}")
            result["errors"].append(f"Gmail notification error: {str(e)}")
    else:
        logger.info("신규/마감임박 공고 없음 - 알림 스킵")

    return result


def execute_workflow():
    """동기 래퍼 - APScheduler에서 호출"""
    scheduler_logger.info("정부 지원사업 모니터링 시작...")
    try:
        result = asyncio.run(run_workflow())
        scheduler_logger.info(f"완료: {json.dumps(result, ensure_ascii=False)}")
    except Exception as e:
        scheduler_logger.error(f"워크플로우 오류: {e}")


def main():
    """메인 함수 - APScheduler로 스케줄 실행"""
    scheduler = BlockingScheduler()

    # 매일 9시 실행
    scheduler.add_job(
        execute_workflow,
        CronTrigger(hour=9, minute=0),
        id='gov_funding_monitor',
        name='Government Funding Monitor'
    )

    scheduler_logger.info("스케줄러 시작: 매일 9시 정부 지원사업 모니터링")
    scheduler_logger.info("즉시 1회 테스트 실행...")
    execute_workflow()

    scheduler.start()


if __name__ == "__main__":
    main()
