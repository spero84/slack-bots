#!/usr/bin/env python3
"""정부 지원사업 공고 모니터링 - Standalone 스크립트

APScheduler로 매일 9시(KST)에 실행됩니다.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from . import crawlers as crawler_module
from .analyzers import BedrockUnavailableError, deadline_filter, filter_with_bedrock, keyword_filter
from .crawlers import BaseCrawler
from .notifiers import send_gmail_notification, send_slack_notification
from .storage import (
    Announcement,
    NotificationPayload,
    TitleMatcher,
    canon_title,
    deduplicate_announcements,
    is_similar_title,
)
from .storage.seen_cache import SeenCache
from .storage.vector_storage import S3VectorStorage, VectorUpsertError, announcement_from_metadata
from .utils import get_config, logger
from .utils.dates import calculate_d_day, now_kst, today_kst

scheduler_logger = logging.getLogger(__name__)

# (소스, 크롤러 클래스명, 최대 수집 건수) — 순서가 중복 제거 우선순위
CRAWLER_SPECS = [
    ("kstartup", "KStartupCrawler", 300),
    ("bizinfo", "BizinfoCrawler", 300),
    ("nipa", "NipaCrawler", 50),
    ("nia", "NiaCrawler", 50),
    ("iitp", "IitpCrawler", 50),
    ("jointips", "JointipsCrawler", 50),
    ("msit", "MsitCrawler", 50),
    ("mss", "MssCrawler", 50),
    ("motie", "MotieCrawler", 50),
]

# D-day가 마일스톤을 처음 지나는 날 리마인더 (예: 이전 D-8 → 오늘 D-7)
DEADLINE_MILESTONES = [7, 3]

# 같은 제목이 다른 소스/ID로 저장돼 있어도, 그 공고가 마감됐거나 이 기간 동안 다시 보이지 않았으면
# 별개의 새 공고로 본다 (예: 매년 같은 제목으로 다시 나오는 공고)
TITLE_MATCH_MAX_AGE_DAYS = 60

# 실행당 Bedrock 분석 상한 (첫 배포처럼 신규 후보가 몰릴 때 비용 제한, 초과분은 다음 실행으로)
MAX_BEDROCK_CALLS_PER_RUN = int(os.environ.get("GOV_FUNDING_MAX_BEDROCK_CALLS", "150"))

# 1회 실행 제한 시간 (멈춘 실행이 다음 날 스케줄까지 막지 않게). 첫 배포 실행이 약 16분
RUN_TIMEOUT_SECONDS = int(os.environ.get("GOV_FUNDING_RUN_TIMEOUT", "7200"))

# Slack 수집 이상 메모 1건 최대 길이 (예외 메시지 전체가 채널에 올라가지 않게)
MAX_OPS_NOTE_CHARS = 200


def vector_key(ann: Announcement) -> str:
    """벡터 저장소 키"""
    return f"{ann.source.value}_{ann.id}"


def create_crawlers() -> list[tuple[str, BaseCrawler, int]]:
    """사용 가능한 크롤러 생성 (Playwright 미설치 시 해당 크롤러는 import되지 않아 제외)"""
    created = []
    for name, class_name, max_items in CRAWLER_SPECS:
        crawler_cls = getattr(crawler_module, class_name, None)
        if crawler_cls is None:
            logger.warning(f"{name} 크롤러 사용 불가 (Playwright 미설치) - 스킵")
            continue
        created.append((name, crawler_cls(), max_items))
    return created


async def _safe_crawl(name: str, crawler: BaseCrawler, max_items: int, result: dict) -> list[Announcement]:
    """크롤러 1개 실행 — 한 소스가 실패해도 나머지 소스는 계속 진행"""
    try:
        items = await crawler.crawl(max_items=max_items)
    except Exception as e:
        logger.error(f"{name} 크롤링 실패: {e}")
        result["errors"].append(f"{name} 크롤링 실패: {e}")
        return []

    if not items:
        result["warnings"].append(f"{name}: 0건")
    return items


async def _close_all(crawlers: list[tuple[str, BaseCrawler, int]]):
    """크롤러 리소스 정리 (하나가 실패해도 나머지는 정리)"""
    for name, crawler, _ in crawlers:
        try:
            await crawler.close()
        except Exception as e:
            logger.warning(f"{name} 크롤러 종료 오류: {e}")


def _is_active_title_match(meta: dict) -> bool:
    """제목 일치로 '이미 알린 공고'로 볼 수 있는 저장 항목인지 (마감 전이고 최근에 수집됨)"""
    deadline = meta.get("deadline") or ""
    try:
        if deadline and datetime.fromisoformat(deadline).date() < today_kst():
            return False
        last_seen = datetime.fromisoformat(meta.get("crawled_at", ""))
    except ValueError:
        return False
    return last_seen >= now_kst() - timedelta(days=TITLE_MATCH_MAX_AGE_DAYS)


def _parse_iso(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def restore_stored_deadlines(announcements: list[Announcement], index: dict[str, dict]):
    """목록에 마감일이 없는 공고(MSIT/MOTIE/NIA 등)에 저장된 마감일 복원

    이 소스들의 마감일은 첨부 공고문에만 있어 처음 분석할 때 Bedrock 단계에서 추출해 저장한다.
    다음 실행부터는 저장된 공고라 상세를 다시 읽지 않으므로, 복원하지 않으면 재저장 때 마감일이
    지워지고 D-7/D-3 리마인더가 영영 나가지 않는다. 마감일 필터 전에 호출해 마감된 공고도 걸러낸다.
    """
    today = today_kst()
    for ann in announcements:
        if ann.deadline is not None:
            continue
        deadline = _parse_iso((index.get(vector_key(ann)) or {}).get("deadline"))
        if deadline is not None:
            ann.deadline = deadline
            ann.d_day = calculate_d_day(deadline, today)


def split_by_index(
    announcements: list[Announcement],
    index: dict[str, dict],
    threshold: float,
) -> tuple[list[Announcement], list[Announcement], list[Announcement], dict[str, dict]]:
    """키워드 필터를 통과한 공고를 저장 여부로 분류

    Returns:
        (stored, title_matched, fresh, twins)
        - stored: 같은 키로 이미 저장된 공고. 저장된 점수/요약을 재사용하고 Bedrock은 생략한다.
        - title_matched: 키는 처음이지만 같은 사업(같거나 유사한 제목)이 다른 소스/ID로 이미 저장된 공고
          (마감 전이고 최근 60일 안에 수집된 항목만). 재알림하지 않는다.
        - fresh: 처음 보는 공고. Bedrock 분석 대상.
        - twins: title_matched 공고 키 → 이미 저장된 같은 사업의 메타데이터 (리마인더의 직전 D-day 기준)
        stored/title_matched 중 저장 당시 점수가 임계값 미만인 공고는 결과에서 제외한다.
    """
    # 같은 제목이 여러 개면 점수가 높은 항목이 먼저 매칭되도록 정렬
    active = sorted(
        (meta for meta in index.values() if meta.get("title") and _is_active_title_match(meta)),
        key=lambda meta: -(meta.get("relevance_score") or 0),
    )
    by_title = TitleMatcher((meta["title"], meta) for meta in active)

    stored, matched, fresh = [], [], []
    twins: dict[str, dict] = {}
    for ann in announcements:
        meta = index.get(vector_key(ann))
        bucket = stored
        if meta is None:
            meta = by_title.find(ann.title)
            bucket = matched
        if meta is None:
            fresh.append(ann)
            continue

        score = meta.get("relevance_score") or 0.0
        if score < threshold:
            continue
        ann.relevance_score = score
        ann.summary = meta.get("summary") or None
        if bucket is matched:
            twins[vector_key(ann)] = meta
            if ann.deadline is None and _parse_iso(meta.get("deadline")):
                ann.deadline = _parse_iso(meta.get("deadline"))
                ann.d_day = calculate_d_day(ann.deadline, today_kst())
        bucket.append(ann)

    return stored, matched, fresh, twins


def stale_reminder_candidates(
    index: dict[str, dict],
    crawled_keys: set[str],
    crawled_titles: TitleMatcher,
    threshold: float,
) -> list[Announcement]:
    """오늘 크롤링되지 않은 저장 공고 중 마감이 가까운 것

    기업마당처럼 최근 등록분만 수집하는 소스는 마감 직전 공고가 목록 수집 범위 밖에 있다.
    저장된 마감일로 D-day를 다시 계산해 리마인더 후보로 쓴다.
    같은 사업이 오늘 다른 키로 수집됐으면(ID 체계 변경, 다른 소스, 연장 재공고) 그쪽이 기준이므로 제외한다.

    Args:
        crawled_keys: 오늘 수집된 모든 공고 키 (중복 제거 전)
        crawled_titles: 오늘 수집된 모든 공고 제목 (같거나 유사한 제목이면 같은 사업으로 본다)
    """
    today = today_kst()
    candidates = []
    for key, meta in index.items():
        if key in crawled_keys or not meta.get("deadline"):
            continue
        if crawled_titles.find(meta.get("title") or "") is not None:
            continue
        if (meta.get("relevance_score") or 0) < threshold:
            continue
        ann = announcement_from_metadata(key, meta)
        if ann is None or ann.deadline is None:
            continue
        ann.d_day = calculate_d_day(ann.deadline, today)
        if ann.d_day is not None and 0 < ann.d_day <= max(DEADLINE_MILESTONES):
            candidates.append(ann)
    return candidates


def find_deadline_reminders(candidates: list[Announcement], prev_d_days: dict[str, float]) -> list[Announcement]:
    """오늘 마감 마일스톤(D-7, D-3)을 지나는 공고

    직전 저장 시점의 D-day(prev)가 마일스톤보다 컸고 오늘 마일스톤 이하가 되면 알린다.
    실행을 하루 건너뛰어도(prev D-8 → 오늘 D-6) 놓치지 않는다.
    같은 사업이 여러 키로 후보에 있으면 한 번만 알린다.

    Args:
        prev_d_days: 공고 키 → 직전 저장 D-day (-1: 마감일 없었음, 키 없음: 처음 저장)
    """
    reminders = []
    for ann in candidates:
        if ann.d_day is None or ann.d_day <= 0:
            continue
        prev_d_day = prev_d_days.get(vector_key(ann), -1)
        for milestone in DEADLINE_MILESTONES:
            if ann.d_day <= milestone and (prev_d_day == -1 or prev_d_day > milestone):
                if not any(is_similar_title(ann.title, r.title) for r in reminders):
                    reminders.append(ann)
                    logger.info(f"마감 리마인더: {ann.title} (D-{ann.d_day}, 마일스톤: D-{milestone})")
                break
    return reminders


def select_for_bedrock(
    fresh: list[Announcement],
    seen_cache: SeenCache,
    limit: int,
) -> tuple[list[Announcement], int, int]:
    """Bedrock 분석 대상 선정

    최근 탈락 판정을 받은 공고는 다시 분석하지 않는다.
    상한을 넘으면 최근 게시 → 마감 임박 순으로 우선 분석하고 나머지는 다음 실행으로 미룬다.

    Returns:
        (분석 대상, 탈락 캐시로 생략한 건수, 상한 초과로 미룬 건수)
    """
    candidates = [a for a in fresh if not seen_cache.is_rejected(vector_key(a), canon_title(a.title))]
    cached = len(fresh) - len(candidates)
    if len(candidates) <= limit:
        return candidates, cached, 0

    candidates.sort(key=lambda a: (
        -(a.posted_date.timestamp() if a.posted_date else 0),
        a.d_day if a.d_day is not None else float("inf"),
    ))
    return candidates[:limit], cached, len(candidates) - limit


def record_bedrock_results(
    analyzed: list[Announcement],
    passed: list[Announcement],
    seen_cache: SeenCache,
):
    """탈락/마감 판정을 캐시에 기록 (분석 실패로 점수가 없는 공고는 기록하지 않아 다음에 재시도)"""
    passed_keys = {vector_key(a) for a in passed}
    today = today_kst()
    for ann in analyzed:
        key = vector_key(ann)
        if key in passed_keys:
            seen_cache.discard(key)
        elif ann.relevance_score is not None:
            seen_cache.record(key, canon_title(ann.title), ann.relevance_score, deadline=ann.deadline)
        elif ann.deadline is not None and ann.deadline.date() < today:
            seen_cache.record(key, canon_title(ann.title), 0.0)


def _preview(announcements: list[Announcement]) -> list[dict]:
    """dry-run 결과 출력용 요약"""
    return [
        {
            "source": a.source.value,
            "title": a.title,
            "d_day": a.d_day,
            "score": a.relevance_score,
            "url": a.url,
        }
        for a in announcements
    ]


def _sort_by_deadline(announcements: list[Announcement]):
    """마감일순 정렬 (가까운 마감일 먼저, 마감일 없는 공고는 맨 뒤)"""
    announcements.sort(key=lambda a: a.d_day if a.d_day is not None else float("inf"))


def ops_notes(result: dict) -> list[str]:
    """Slack에 함께 표시할 수집 이상 메모 (오류 + 경고, 길이 제한)"""
    notes = []
    for note in result["errors"] + result["warnings"]:
        note = str(note)
        notes.append(note if len(note) <= MAX_OPS_NOTE_CHARS else note[:MAX_OPS_NOTE_CHARS] + "…")
    return notes


async def _notify_errors_only(result: dict, quiet: bool = False):
    """공고 알림 없이 수집 이상만 Slack에 알림 (quiet: dry-run/seed에서는 보내지 않음)

    오류뿐 아니라 '0건' 소스 경고도 보낸다 — 알릴 공고가 없는 날 죽은 소스가 조용히 묻히지 않게.
    """
    has_dead_source = any(w.endswith(": 0건") for w in result["warnings"])
    if quiet or not (result["errors"] or has_dead_source):
        return
    try:
        result["notifications"]["slack"] = await send_slack_notification(
            NotificationPayload(), ops_notes=ops_notes(result),
        )
    except Exception as e:
        logger.error(f"Slack 오류 알림 실패: {e}")


async def run_workflow(force_resend: bool = False, dry_run: bool = False, seed: bool = False) -> dict:
    """메인 워크플로우 실행

    1. 소스별 공고 크롤링 (소스별로 실패 격리)
    2. 중복 제거 → 마감일 필터 → 키워드 필터
    3. S3 Vectors 조회로 저장/신규 공고 분류 (저장된 공고는 Bedrock 생략)
    4. 신규 공고만 Bedrock 분석 (상세 내용/첨부파일 포함, 최근 탈락 공고는 생략)
    5. 신규 공고 + 마감 리마인더 Slack/Gmail 알림 (수집 이상도 함께 알림)
    6. 알림 성공 후 벡터 저장 (실패 시 저장하지 않아 다음 실행에서 재시도)

    Args:
        force_resend: 저장 여부와 무관하게 관련 공고 전체를 신규로 알림
        dry_run: 알림/벡터 저장/캐시 저장 없이 결과만 반환 (result["preview"])
        seed: 알림 없이 벡터 저장/캐시 저장만 수행 (기존 공고를 '알린 것'으로 등록)

    Returns:
        실행 결과 요약
    """
    config = get_config()
    threshold = config.relevance_threshold
    result = {
        "timestamp": datetime.now().isoformat(),
        "crawled": {},
        "filtered": 0,
        "new_announcements": 0,
        "deadline_soon": 0,
        "notifications": {"slack": False, "gmail": False},
        "warnings": [],
        "errors": [],
    }

    vector_storage = S3VectorStorage()
    seen_cache = SeenCache().load()
    crawlers = create_crawlers()

    try:
        # 1. 크롤링
        crawled: dict[str, list[Announcement]] = {}
        for name, crawler, max_items in crawlers:
            crawled[name] = await _safe_crawl(name, crawler, max_items, result)
            result["crawled"][name] = len(crawled[name])
        logger.info("크롤링 완료 - " + ", ".join(f"{n}: {c}건" for n, c in result["crawled"].items()))

        crawled_all = [a for items in crawled.values() for a in items]
        all_announcements = deduplicate_announcements(*crawled.values())
        if not all_announcements:
            logger.warning("크롤링 결과 없음")
            result["errors"].append("모든 소스 크롤링 결과 0건")
            await _notify_errors_only(result, quiet=dry_run or seed)
            return result

        # 2. 저장된 공고 조회
        try:
            if not dry_run:
                vector_storage.ensure_index()
            index = vector_storage.get_existing_index()
        except Exception as e:
            # 저장 공고를 모르면 신규를 판별할 수 없다. 전부 신규로 보내면 수십 건이 재알림되므로
            # 이번 실행은 공고 알림을 보내지 않고 오류만 알린다.
            logger.error(f"벡터 저장소 조회 실패 - 이번 실행은 공고 알림 생략: {e}")
            result["errors"].append(f"벡터 저장소 조회 실패: {e}")
            await _notify_errors_only(result, quiet=dry_run or seed)
            return result

        # 3. 마감일 + 키워드 필터 (저장된 마감일 복원 후)
        restore_stored_deadlines(all_announcements, index)
        keyword_filtered = keyword_filter(deadline_filter(all_announcements))

        stored, matched, fresh, twins = split_by_index(keyword_filtered, index, threshold)
        logger.info(
            f"기존 벡터 {len(index)}건 - 저장된 공고 {len(stored)}건, "
            f"다른 소스로 저장된 공고 {len(matched)}건, 신규 후보 {len(fresh)}건"
        )

        # 4. Bedrock 필터링 (신규 후보만, 크롤러가 살아있는 동안 상세 내용 수집)
        to_analyze, cached, deferred = select_for_bedrock(fresh, seen_cache, MAX_BEDROCK_CALLS_PER_RUN)
        if cached:
            logger.info(f"최근 Bedrock 탈락 공고 {cached}건 재분석 생략")
        if deferred:
            logger.warning(f"Bedrock 분석 상한 {MAX_BEDROCK_CALLS_PER_RUN}건 초과 - {deferred}건은 다음 실행으로 연기")
            result["warnings"].append(f"Bedrock 분석 {deferred}건 다음 실행으로 연기")

        crawler_map = {name: crawler for name, crawler, _ in crawlers}
        try:
            new_relevant = (
                await filter_with_bedrock(to_analyze, threshold=threshold, crawlers=crawler_map)
                if to_analyze else []
            )
            record_bedrock_results(to_analyze, new_relevant, seen_cache)
        except BedrockUnavailableError as e:
            # 분석 없이 키워드 통과분을 보내면 수십 건이 무검증으로 올라간다 → 신규 판정은 다음 실행으로 미룸
            logger.error(f"Bedrock 사용 불가 - 신규 공고 판정 생략: {e}")
            result["errors"].append(str(e))
            new_relevant = []
    finally:
        await _close_all(crawlers)

    relevant = stored + matched + new_relevant
    result["filtered"] = len(relevant)

    # 5. 신규 / 마감 리마인더
    payload = NotificationPayload()
    if force_resend:
        logger.info("강제 재전송 모드: 모든 관련 공고를 신규로 취급")
        payload.new_announcements = list(relevant)
        stale = []
    else:
        payload.new_announcements = list(new_relevant)
        stale = stale_reminder_candidates(
            index,
            crawled_keys={vector_key(a) for a in crawled_all},
            crawled_titles=TitleMatcher((a.title, True) for a in crawled_all),
            threshold=threshold,
        )
        prev_d_days = {key: meta.get("d_day", -1) for key, meta in index.items()}
        prev_d_days.update({key: meta.get("d_day", -1) for key, meta in twins.items()})
        payload.deadline_soon = find_deadline_reminders(stored + matched + stale, prev_d_days)

    _sort_by_deadline(payload.new_announcements)
    _sort_by_deadline(payload.deadline_soon)
    result["new_announcements"] = len(payload.new_announcements)
    result["deadline_soon"] = len(payload.deadline_soon)

    if dry_run:
        result["preview"] = {
            "new": _preview(payload.new_announcements),
            "deadline_soon": _preview(payload.deadline_soon),
        }
        logger.info("dry-run: 알림/벡터 저장/캐시 저장 생략")
        return result

    # 6. 알림
    slack_ok = True
    if seed:
        logger.info(f"seed 모드: 알림 없이 저장만 수행 (신규 {len(payload.new_announcements)}건)")
    elif payload.has_content:
        try:
            slack_ok = await send_slack_notification(payload, ops_notes=ops_notes(result) or None)
        except Exception as e:
            slack_ok = False
            logger.error(f"Slack 알림 오류: {e}")
        result["notifications"]["slack"] = slack_ok
        if not slack_ok:
            result["errors"].append("Slack 알림 실패")

        try:
            gmail_result = await send_gmail_notification(payload)
            result["notifications"]["gmail"] = "skipped" if gmail_result is None else gmail_result
        except Exception as e:
            logger.error(f"Gmail 알림 오류: {e}")
            result["errors"].append(f"Gmail notification error: {e}")
    else:
        logger.info("신규/마감임박 공고 없음 - 알림 스킵")
        await _notify_errors_only(result)

    # 7. 벡터 저장 — 알림이 실패하면 저장하지 않는다 (신규/리마인더를 다음 실행에서 다시 보냄)
    # 수집 범위 밖 공고(stale)도 리마인더 여부와 관계없이 D-day를 갱신해 둔다 (같은 사업의 다른 키가
    # 중복 리마인더로 하루 늦게 다시 알리는 것 방지)
    if slack_ok:
        to_upsert = list({vector_key(a): a for a in relevant + stale}.values())
        try:
            vector_storage.upsert_announcements(to_upsert)
        except VectorUpsertError as e:
            logger.error(f"벡터 저장 오류: {e}")
            result["errors"].append(f"Vector storage error: {e}")
            # 이미 알린 신규 공고가 저장되지 않으면 다음 실행에서 다시 '신규'로 알림된다 → 캐시로 막음
            failed = set(e.failed_keys)
            for ann in payload.new_announcements:
                if vector_key(ann) in failed:
                    seen_cache.record(vector_key(ann), canon_title(ann.title), -1.0, deadline=ann.deadline)
        except Exception as e:
            logger.error(f"벡터 저장 오류: {e}")
            result["errors"].append(f"Vector storage error: {e}")
    else:
        logger.warning("Slack 알림 실패 - 벡터 저장 생략 (다음 실행에서 재시도)")
    seen_cache.save()

    return result


def execute_workflow(force_resend: bool = False, dry_run: bool = False, seed: bool = False):
    """동기 래퍼 - APScheduler에서 호출"""
    scheduler_logger.info("정부 지원사업 모니터링 시작...")
    if force_resend:
        scheduler_logger.info("강제 재전송 모드 활성화")
    try:
        result = asyncio.run(asyncio.wait_for(
            run_workflow(force_resend=force_resend, dry_run=dry_run, seed=seed),
            timeout=RUN_TIMEOUT_SECONDS,
        ))
        scheduler_logger.info(f"완료: {json.dumps(result, ensure_ascii=False)}")
        return result
    except Exception as e:
        scheduler_logger.exception(f"워크플로우 오류: {e}")
        if dry_run or seed:
            return None
        try:
            asyncio.run(send_slack_notification(NotificationPayload(), ops_notes=[f"워크플로우 오류: {e}"]))
        except Exception as notify_error:
            scheduler_logger.error(f"Slack 오류 알림 실패: {notify_error}")


def main(argv: list[str] | None = None):
    """메인 함수 - APScheduler로 스케줄 실행

    시작 시 1회 실행(GOV_FUNDING_RUN_ON_START, 기본 1)은 저장된 공고와 비교하므로 신규 공고만 알린다.
    기존 공고까지 전부 다시 보내려면 GOV_FUNDING_FORCE_RESEND_ON_START=1.

    수동 실행 옵션 (스케줄러 없이 1회 실행 후 종료):
        --dry-run  알림/저장 없이 결과 확인
        --seed     알림 없이 저장만 (현재 공고를 '알린 것'으로 등록)
        --once     1회 실제 실행
    """
    parser = argparse.ArgumentParser(description="정부 지원사업 공고 모니터링")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="1회 실행 후 종료")
    mode.add_argument("--dry-run", action="store_true", help="알림/저장 없이 1회 실행 결과만 출력")
    mode.add_argument("--seed", action="store_true", help="알림 없이 벡터/캐시 저장만 1회 실행")
    args = parser.parse_args(argv)

    if args.once or args.dry_run or args.seed:
        # systemd는 EnvironmentFile로 .env를 넣어주지만 수동 실행은 아니다. 기본 버킷/임계값으로
        # 돌면 운영 저장소가 아닌 곳과 비교하므로 막는다.
        if not os.environ.get("S3_BUCKET"):
            parser.error("환경변수가 없습니다. 먼저 `set -a; . ./.env; set +a`로 .env를 불러오세요.")
        result = execute_workflow(dry_run=args.dry_run, seed=args.seed)
        if result is None:
            sys.exit(1)
        if args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    scheduler = BlockingScheduler(timezone="Asia/Seoul")

    # 매일 9시 실행 (재시작 등으로 정각을 놓쳐도 1시간 안이면 1회 실행)
    scheduler.add_job(
        execute_workflow,
        CronTrigger(hour=9, minute=0, timezone="Asia/Seoul"),
        id="gov_funding_monitor",
        name="Government Funding Monitor",
        misfire_grace_time=3600,
        coalesce=True,
    )

    scheduler_logger.info("스케줄러 시작: 매일 9시 정부 지원사업 모니터링")
    if os.environ.get("GOV_FUNDING_RUN_ON_START", "1") == "1":
        force_resend = os.environ.get("GOV_FUNDING_FORCE_RESEND_ON_START", "0") == "1"
        scheduler_logger.info(f"즉시 1회 실행 (강제 재전송: {force_resend})...")
        execute_workflow(force_resend=force_resend)

    scheduler.start()


if __name__ == "__main__":
    main()
