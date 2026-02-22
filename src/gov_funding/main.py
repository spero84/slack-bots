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

from .analyzers import deadline_filter, filter_with_bedrock, keyword_filter
from .crawlers import BizinfoCrawler, NipaCrawler, NiaCrawler, JointipsCrawler, MssCrawler
from .notifiers import send_gmail_notification, send_slack_notification
from .storage import NotificationPayload, Source, deduplicate_announcements
from .storage.vector_storage import S3VectorStorage
from .utils import get_config, logger

# Playwright is optional
try:
    from .crawlers import KStartupCrawler
    KSTARTUP_AVAILABLE = True
except ImportError:
    KSTARTUP_AVAILABLE = False
    logger.warning("Playwright not available - K-Startup crawling disabled")

try:
    from .crawlers import IitpCrawler
    IITP_AVAILABLE = True
except ImportError:
    IITP_AVAILABLE = False
    logger.warning("Playwright not available - IITP crawling disabled")

try:
    from .crawlers import MsitCrawler
    MSIT_AVAILABLE = True
except ImportError:
    MSIT_AVAILABLE = False
    logger.warning("Playwright not available - MSIT crawling disabled")

try:
    from .crawlers import MotieCrawler
    MOTIE_AVAILABLE = True
except ImportError:
    MOTIE_AVAILABLE = False
    logger.warning("Playwright not available - MOTIE crawling disabled")

# 스케줄러 로깅
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
scheduler_logger = logging.getLogger(__name__)


async def run_workflow(force_resend: bool = False) -> dict:
    """메인 워크플로우 실행

    1. K-Startup, 기업마당, NIPA, NIA, IITP, JOINTIPS, MSIT, MSS, MOTIE에서 공고 크롤링
    2. 중복 제거 및 병합
    3. 키워드 + Bedrock 필터링
    4. S3 Vectors 비교로 신규/마감임박 식별
    5. 벡터 임베딩 저장
    6. Slack/Gmail 알림 전송

    Returns:
        실행 결과 요약
    """
    config = get_config()
    vector_storage = S3VectorStorage()
    vector_storage.ensure_index()

    result = {
        "timestamp": datetime.now().isoformat(),
        "crawled": {
            "kstartup": 0, "bizinfo": 0, "nipa": 0, "nia": 0, "iitp": 0, "jointips": 0,
            "msit": 0, "mss": 0, "motie": 0,
        },
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
    nia_announcements = []
    iitp_announcements = []
    jointips_announcements = []
    msit_announcements = []
    mss_announcements = []
    motie_announcements = []

    bizinfo_crawler = BizinfoCrawler()
    nipa_crawler = NipaCrawler()
    nia_crawler = NiaCrawler()
    jointips_crawler = JointipsCrawler()
    mss_crawler = MssCrawler()
    kstartup_crawler = KStartupCrawler() if KSTARTUP_AVAILABLE else None
    iitp_crawler = IitpCrawler() if IITP_AVAILABLE else None
    msit_crawler = MsitCrawler() if MSIT_AVAILABLE else None
    motie_crawler = MotieCrawler() if MOTIE_AVAILABLE else None

    try:
        # Bizinfo 크롤링 (항상 실행)
        bizinfo_announcements = await bizinfo_crawler.crawl(max_items=50)
        result["crawled"]["bizinfo"] = len(bizinfo_announcements)

        # NIPA 크롤링 (항상 실행)
        nipa_announcements = await nipa_crawler.crawl(max_items=50)
        result["crawled"]["nipa"] = len(nipa_announcements)

        # NIA 크롤링 (항상 실행)
        nia_announcements = await nia_crawler.crawl(max_items=50)
        result["crawled"]["nia"] = len(nia_announcements)

        # K-Startup 크롤링 (Playwright 사용 가능한 경우만)
        if kstartup_crawler:
            kstartup_announcements = await kstartup_crawler.crawl(max_items=50)
            result["crawled"]["kstartup"] = len(kstartup_announcements)
        else:
            logger.info("K-Startup 크롤링 스킵 (Playwright 미설치)")

        # IITP 크롤링 (Playwright 사용 가능한 경우만)
        if iitp_crawler:
            iitp_announcements = await iitp_crawler.crawl(max_items=50)
            result["crawled"]["iitp"] = len(iitp_announcements)
        else:
            logger.info("IITP 크롤링 스킵 (Playwright 미설치)")

        # JOINTIPS 크롤링 (항상 실행)
        jointips_announcements = await jointips_crawler.crawl(max_items=50)
        result["crawled"]["jointips"] = len(jointips_announcements)

        # MSIT 크롤링 (Playwright 사용 가능한 경우만)
        if msit_crawler:
            msit_announcements = await msit_crawler.crawl(max_items=50)
            result["crawled"]["msit"] = len(msit_announcements)
        else:
            logger.info("MSIT 크롤링 스킵 (Playwright 미설치)")

        # MSS 크롤링 (항상 실행)
        mss_announcements = await mss_crawler.crawl(max_items=50)
        result["crawled"]["mss"] = len(mss_announcements)

        # MOTIE 크롤링 (Playwright 사용 가능한 경우만)
        if motie_crawler:
            motie_announcements = await motie_crawler.crawl(max_items=50)
            result["crawled"]["motie"] = len(motie_announcements)
        else:
            logger.info("MOTIE 크롤링 스킵 (Playwright 미설치)")

        logger.info(
            f"크롤링 완료 - K-Startup: {len(kstartup_announcements)}건, "
            f"Bizinfo: {len(bizinfo_announcements)}건, "
            f"NIPA: {len(nipa_announcements)}건, "
            f"NIA: {len(nia_announcements)}건, "
            f"IITP: {len(iitp_announcements)}건, "
            f"JOINTIPS: {len(jointips_announcements)}건, "
            f"MSIT: {len(msit_announcements)}건, "
            f"MSS: {len(mss_announcements)}건, "
            f"MOTIE: {len(motie_announcements)}건"
        )

    except Exception as e:
        logger.error(f"크롤링 오류: {e}")
        result["errors"].append(f"Crawling error: {str(e)}")
    finally:
        await bizinfo_crawler.close()
        await nipa_crawler.close()
        await nia_crawler.close()
        await jointips_crawler.close()
        await mss_crawler.close()
        if kstartup_crawler:
            await kstartup_crawler.close()
        if iitp_crawler:
            await iitp_crawler.close()
        if msit_crawler:
            await msit_crawler.close()
        if motie_crawler:
            await motie_crawler.close()

    if not any([kstartup_announcements, bizinfo_announcements, nipa_announcements,
                nia_announcements, iitp_announcements, jointips_announcements,
                msit_announcements, mss_announcements, motie_announcements]):
        logger.warning("크롤링 결과 없음")
        return result

    # 2. 중복 제거 및 병합 (우선순위: K-Startup > Bizinfo > NIPA > NIA > IITP > JOINTIPS > MSIT > MSS > MOTIE)
    all_announcements = deduplicate_announcements(
        kstartup_announcements,
        bizinfo_announcements,
        nipa_announcements,
        nia_announcements,
        iitp_announcements,
        jointips_announcements,
        msit_announcements,
        mss_announcements,
        motie_announcements,
    )

    # 2.5. 마감일 필터링 (2개월 이내만)
    deadline_filtered = deadline_filter(all_announcements)

    # 3. 키워드 필터링 (1차)
    keyword_filtered = keyword_filter(deadline_filtered)

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

    # 5. S3 Vectors 비교 (신규/마감임박 식별)
    combined_payload = NotificationPayload()

    try:
        if force_resend:
            # 재시작 시 강제 재전송: 모든 필터링된 공고를 신규로 취급
            logger.info("강제 재전송 모드: 모든 공고를 신규로 취급")
            combined_payload.new_announcements = list(final_filtered)
        else:
            existing_keys = vector_storage.get_existing_keys()
            logger.info(f"기존 벡터 수: {len(existing_keys)}건")

            # 신규 공고 식별
            for ann in final_filtered:
                key = f"{ann.source.value}_{ann.id}"
                if key not in existing_keys:
                    combined_payload.new_announcements.append(ann)

            # 마감임박 식별 (마일스톤 기반: D-7, D-3 시점 알림)
            DEADLINE_MILESTONES = [7, 3]
            existing_filtered_keys = [
                f"{a.source.value}_{a.id}"
                for a in final_filtered
                if f"{a.source.value}_{a.id}" in existing_keys
            ]
            if existing_filtered_keys:
                prev_metadata = vector_storage.get_vectors_metadata(existing_filtered_keys)
                for ann in final_filtered:
                    key = f"{ann.source.value}_{ann.id}"
                    if key not in prev_metadata:
                        continue
                    if ann.d_day is None or ann.d_day <= 0:
                        continue

                    prev_d_day = prev_metadata[key].get("d_day", -1)

                    # 마일스톤 전환 감지: prev_d_day > milestone >= current d_day
                    for milestone in DEADLINE_MILESTONES:
                        if ann.d_day <= milestone and (prev_d_day == -1 or prev_d_day > milestone):
                            combined_payload.deadline_soon.append(ann)
                            logger.info(f"마감 리마인더: {ann.title} (D-{ann.d_day}, 마일스톤: D-{milestone})")
                            break

        # 벡터 저장 (전체 upsert - 신규+기존 모두 업데이트)
        vector_storage.upsert_announcements(final_filtered)

    except Exception as e:
        logger.error(f"벡터 저장/비교 오류: {e}")
        result["errors"].append(f"Vector storage error: {str(e)}")
        # 벡터 저장 실패 시에도 모든 공고를 신규로 취급
        if not combined_payload.new_announcements:
            combined_payload.new_announcements = list(final_filtered)

    # 마감일순 정렬 (가까운 마감일 먼저, 마감일 없는 공고는 맨 뒤)
    combined_payload.new_announcements.sort(
        key=lambda a: a.d_day if a.d_day is not None else float('inf')
    )
    combined_payload.deadline_soon.sort(
        key=lambda a: a.d_day if a.d_day is not None else float('inf')
    )

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


def execute_workflow(force_resend: bool = False):
    """동기 래퍼 - APScheduler에서 호출"""
    scheduler_logger.info("정부 지원사업 모니터링 시작...")
    if force_resend:
        scheduler_logger.info("강제 재전송 모드 활성화")
    try:
        result = asyncio.run(run_workflow(force_resend=force_resend))
        scheduler_logger.info(f"완료: {json.dumps(result, ensure_ascii=False)}")
    except Exception as e:
        scheduler_logger.error(f"워크플로우 오류: {e}")


def main():
    """메인 함수 - APScheduler로 스케줄 실행"""
    scheduler = BlockingScheduler()

    # 매일 9시 실행
    scheduler.add_job(
        execute_workflow,
        CronTrigger(hour=9, minute=0, timezone='Asia/Seoul'),
        id='gov_funding_monitor',
        name='Government Funding Monitor'
    )

    scheduler_logger.info("스케줄러 시작: 매일 9시 정부 지원사업 모니터링")
    scheduler_logger.info("즉시 1회 실행 (강제 재전송)...")
    execute_workflow(force_resend=True)

    scheduler.start()


if __name__ == "__main__":
    main()
