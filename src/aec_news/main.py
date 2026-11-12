#!/usr/bin/env python3
"""EPC·AEC × AI 뉴스 모니터링 — 매일 08:30 KST에 국내·미국·싱가포르 주요 언론사 기사 링크를 Slack에 정리

파이프라인:
1. Google News RSS 검색 (주제 × 국가 × 언론사 묶음, 약 20회) → 언론사 호스트/게시 시각/제목 언어 필터
2. 이번 실행 안 중복 제거 + 이미 보낸 기사·이미 탈락한 기사 제외
3. Bedrock 관련성 판정 (ai_tech / ai_infra / irrelevant)
4. 통과 기사만 원문 URL 복원 → 원문 URL 기준으로 전송 이력 재확인
5. 한국어 한 줄 요약 + 같은 소식 묶기
6. Slack 전송 → 성공하면 전송 이력/마지막 성공 시각 저장

CLI:
  python -m src.aec_news.main               # 스케줄러 (매일 08:30 KST)
  python -m src.aec_news.main --dry-run     # 검색+판정만, Slack 전송/상태 저장 없이 결과 출력
  python -m src.aec_news.main --once        # 실제 1회 실행 (Slack 전송)
  옵션: --hours N (수집 기간 직접 지정), --no-llm (Bedrock 없이 키워드 규칙), --json (dry-run 결과 JSON)
"""
import argparse
import json
import logging
import math
import sys
import threading
from datetime import datetime, timezone
from typing import Optional

from .config import KST, get_config
from .google_news import GoogleNewsClient, lookback_days, resolve_urls, search_all
from .logger import logger
from .models import NewsItem, Region, canonical_url
from .relevance import TriageOutcome, keyword_fallback, summarize, triage
from .slack_notifier import DigestContext, SlackNotifier, build_messages
from .state import RunState

_run_lock = threading.Lock()


def dedupe_run(items: list[NewsItem]) -> list[NewsItem]:
    """이번 실행 안의 중복 제거 — 같은 Google ID 또는 같은 언론사·같은 제목은 한 건으로 (검색 주제는 합친다)"""
    by_key: dict[str, NewsItem] = {}
    out: list[NewsItem] = []
    for item in items:
        keys = [f"gid:{item.google_id}", item.title_key]
        existing = next((by_key[k] for k in keys if k in by_key), None)
        if existing is not None:
            if item.google_id != existing.google_id and item.google_id not in existing.alias_ids:
                existing.alias_ids.append(item.google_id)
            for theme in item.query_themes:
                if theme not in existing.query_themes:
                    existing.query_themes.append(theme)
            if not existing.snippet and item.snippet:
                existing.snippet = item.snippet
            for k in keys:
                by_key.setdefault(k, existing)
            continue
        for k in keys:
            by_key[k] = item
        out.append(item)
    return out


def dedupe_resolved(items: list[NewsItem]) -> list[NewsItem]:
    """원문 URL이 같은 기사(제목이 조금 다른 사본)를 한 건으로"""
    seen: set[str] = set()
    out: list[NewsItem] = []
    for item in items:
        key = canonical_url(item.url) if item.url else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(item)
    return out


def _item_summary(item: NewsItem) -> dict:
    return {
        "region": item.region.value,
        "outlet": item.outlet_name,
        "published": item.published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
        "title": item.title,
        "url": item.link,
        "score": item.score,
        "theme": item.theme.value if item.theme else None,
        "summary": item.summary_ko,
        "story": item.story,
    }


def run_workflow(dry_run: bool = False, hours: Optional[float] = None, use_llm: bool = True,
                 now: Optional[datetime] = None, state: Optional[RunState] = None,
                 client: Optional[GoogleNewsClient] = None, notifier: Optional[SlackNotifier] = None) -> dict:
    config = get_config()
    now = now or datetime.now(timezone.utc)
    state = state or RunState().load()
    client = client or GoogleNewsClient()
    if hours is not None:
        days = max(1, min(config.max_lookback_days, math.ceil(hours / 24)))
        days_by_region = {r: days for r in Region}
    else:
        days_by_region = {r: lookback_days(r, state.last_success, now, config) for r in Region}
    result: dict = {
        "run_at": now.astimezone(KST).isoformat(),
        "days_by_region": {r.value: d for r, d in days_by_region.items()},
        "hours": hours,
        "dry_run": dry_run,
        "searched": 0,
        "candidates": 0,
        "main": [],
        "infra": [],
        "notification": False,
        "warnings": [],
    }
    logger.info(f"검색 기간(일): {result['days_by_region']}" + (f" · 게시 {hours}시간 이내" if hours else ""))

    # 1. 검색
    search = search_all(now, days_by_region, hours=hours, client=client)
    result["searched"] = len(search.items)
    result["query_stats"] = search.stats
    warnings = list(search.warnings)

    # 2. 중복/이력 제외
    unique = dedupe_run(search.items)
    fresh = [i for i in unique if not state.was_sent(i.history_keys())]
    candidates = [i for i in fresh if not state.is_rejected(i.title_key, now)]
    logger.info(
        f"검색 {len(search.items)}건 → 중복 제거 {len(unique)} → 미전송 {len(fresh)} → 판정 대상 {len(candidates)}"
    )
    result["candidates"] = len(candidates)

    # 3. 관련성 판정
    if not candidates:
        outcome = TriageOutcome()
    elif use_llm:
        outcome = triage(candidates, threshold=config.relevance_threshold)
    else:
        outcome = keyword_fallback(candidates)
        outcome.warnings.append("AI 판정 없이 제목 키워드 규칙으로 골랐습니다 (--no-llm)")
    warnings.extend(outcome.warnings)

    infra_all = sorted(outcome.infra, key=lambda a: -a.published_at.timestamp()) if config.include_infra else []
    infra = infra_all[: config.max_infra_items]
    infra_ids = {id(i) for i in infra}
    overflow_infra = [i for i in outcome.infra if id(i) not in infra_ids]

    # 4. 원문 URL 복원 (통과 기사만) → 원문 URL로 이력·중복 재확인
    cache = state.decode_cache()
    kept, url_warnings = resolve_urls(outcome.main + infra, cache, client=client)
    warnings.extend(url_warnings)
    state.update_decoded(cache, now)
    kept = dedupe_resolved([i for i in kept if not state.was_sent(i.history_keys())])
    main_ids = {id(i) for i in outcome.main}
    main_items = [i for i in kept if id(i) in main_ids]
    infra = [i for i in kept if id(i) in infra_ids]

    # 5. 요약 + 같은 소식 묶기
    if main_items and use_llm:
        warn = summarize(main_items)
        if warn:
            warnings.append(warn)

    result["main"] = [_item_summary(i) for i in main_items]
    result["infra"] = [_item_summary(i) for i in infra]
    result["warnings"] = warnings
    result["google_requests"] = client.requests

    ctx = DigestContext(run_at=now.astimezone(KST), days_by_region=days_by_region, hours=hours,
                        candidates=len(candidates), warnings=warnings)
    messages = build_messages(main_items, infra, ctx)
    result["slack_messages"] = len(messages)

    if dry_run:
        result["slack_preview"] = [{"blocks": m.blocks, "text": m.text} for m in messages]
        return result

    # 6. 전송 → 전송된 메시지에 담긴 기사만 이력 저장 (실패한 메시지의 기사는 다음 실행에서 다시 보낸다)
    delivered = (notifier or SlackNotifier()).send(messages)
    sent_ok = 0 in delivered
    result["notification"] = sent_ok
    if sent_ok and len(delivered) < len(messages):
        result["warnings"].append(f"Slack 스레드 답글 {len(messages) - len(delivered)}개 전송 실패")
    if use_llm:
        # Bedrock 탈락 판정은 전송 여부와 무관하게 기록 (같은 기사를 매일 다시 판정하지 않는다).
        # outcome.unjudged(응답 누락·규칙 탈락)는 기록하지 않아 다음 실행에서 다시 판정한다
        cached = outcome.rejected + (overflow_infra if outcome.llm_ok else [])
        for item in cached:
            state.record_rejected(item.title_key, item.score or 0.0, now)
    if sent_ok:
        for idx in delivered:
            for item in messages[idx].items:
                state.mark_sent(item.history_keys(), now)
        if search.complete:
            state.last_success = now
    state.save()
    return result


def execute_workflow(dry_run: bool = False, hours: Optional[float] = None, use_llm: bool = True) -> Optional[dict]:
    """APScheduler 진입점 — 동시 실행 방지 + 예외 로그"""
    if not _run_lock.acquire(blocking=False):
        logger.warning("이전 실행이 아직 진행 중 - 이번 실행 건너뜀")
        return None
    try:
        logger.info("EPC·AEC AI 뉴스 모니터링 시작")
        result = run_workflow(dry_run=dry_run, hours=hours, use_llm=use_llm)
        log_result = {k: v for k, v in result.items() if k != "slack_preview"}
        logger.info(f"완료: {json.dumps(log_result, ensure_ascii=False, default=str)}")
        return result
    except Exception as e:  # noqa: BLE001
        logger.exception(f"워크플로우 오류: {e}")
        return None
    finally:
        _run_lock.release()


def _preview_text(messages) -> str:
    """dry-run 출력용: Slack 블록을 사람이 읽을 텍스트로 (SlackMessage 또는 JSON으로 읽은 dict)"""
    out = []
    for n, message in enumerate(messages):
        blocks = message["blocks"] if isinstance(message, dict) else message.blocks
        if n:
            out.append("---- (스레드 답글) ----")
        for b in blocks:
            if b["type"] == "header":
                out.append(f"# {b['text']['text']}")
            elif b["type"] == "section":
                out.append(b["text"]["text"])
            elif b["type"] == "context":
                out.append("> " + " ".join(e.get("text", "") for e in b["elements"]))
            elif b["type"] == "divider":
                out.append("────────")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None):
    parser = argparse.ArgumentParser(description="EPC·AEC × AI 뉴스 모니터링")
    parser.add_argument("--dry-run", action="store_true", help="Slack 전송/상태 저장 없이 결과 출력")
    parser.add_argument("--once", action="store_true", help="실제 1회 실행 후 종료 (Slack 전송)")
    parser.add_argument("--hours", type=float, default=None, help="수집 기간(시간) 직접 지정")
    parser.add_argument("--no-llm", action="store_true", help="Bedrock 없이 키워드 규칙으로 선별")
    parser.add_argument("--json", action="store_true", help="dry-run 결과를 JSON으로 출력")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    if args.dry_run:
        result = run_workflow(dry_run=True, hours=args.hours, use_llm=not args.no_llm)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
        else:
            print(_preview_text(result.pop("slack_preview")))
            print()
            print(json.dumps({k: v for k, v in result.items() if k not in ("main", "infra")},
                             ensure_ascii=False, indent=1, default=str))
        return
    if args.once:
        result = execute_workflow(hours=args.hours, use_llm=not args.no_llm)
        sys.exit(0 if result and result.get("notification") else 1)

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    config = get_config()
    scheduler = BlockingScheduler(timezone=KST)
    scheduler.add_job(
        execute_workflow,
        CronTrigger(hour=config.schedule_hour, minute=config.schedule_minute, timezone=KST),
        id="aec_news_monitor",
        name="AEC News Monitor",
        misfire_grace_time=3600,   # 시스템이 잠깐 멈췄다 돌아와도 1시간 안이면 실행
        coalesce=True,
        max_instances=1,
    )
    logger.info(f"스케줄러 시작: 매일 {config.schedule_hour:02d}:{config.schedule_minute:02d} KST")
    if config.run_on_start:
        logger.info("AEC_NEWS_RUN_ON_START=1 — 시작 시 1회 실행")
        execute_workflow()
    scheduler.start()


if __name__ == "__main__":
    main()
