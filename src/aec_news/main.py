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

from .article_text import ArticleFetcher, mention_excerpt
from .config import COMPETITORS, KST, get_config
from .google_news import GoogleNewsClient, lookback_days, names_pattern, resolve_urls, search_all
from .logger import logger
from .models import NewsItem, Region, canonical_url
from .relevance import (
    CompetitorOutcome,
    TriageOutcome,
    competitor_fallback,
    keyword_fallback,
    summarize,
    triage,
    verify_competitors,
)
from .slack_notifier import (
    MAX_SAME_STORY_LINKS,
    DigestContext,
    SlackNotifier,
    build_messages,
    group_stories,
)
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
            # 주제 검색이 먼저 돌아 주제 쪽 사본이 남는다 — 경쟁사 검색의 후보 회사를 잃으면 본문 언급 확인을 못 한다
            for key in item.competitor_hits:
                if key not in existing.competitor_hits:
                    existing.competitor_hits.append(key)
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
        "competitor": item.competitor,
    }


def check_competitor_mentions(items: list[NewsItem], cache: dict[str, str], client, fetcher,
                              max_body_checks: int) -> tuple[list[NewsItem], list[NewsItem], list[NewsItem]]:
    """경쟁사 후보를 (Bedrock 확인 대상, 본문에도 이름 없음, 다음 실행으로 미룸)으로 나눈다

    제목에 후보 회사 이름이 있으면 바로 확인 대상. 없으면 원문 URL을 복원해 본문을 받고, 이름 주변을 발췌해 붙인다
    (Google은 사이드바·관련 기사 문구로도 매칭한다). 본문 확인은 최신순으로 실행당 max_body_checks건까지.
    """
    comp_by_key = {c.key: c for c in COMPETITORS}
    to_verify: list[NewsItem] = []
    body: list[NewsItem] = []
    for item in items:
        pats = [p for k in item.competitor_hits if (c := comp_by_key.get(k)) and (p := names_pattern(c))]
        if any(p.search(f"{item.title} {item.snippet}") for p in pats):
            item.mention = "title"
            to_verify.append(item)
        else:
            body.append(item)
    # 국내 후보 먼저 (실측: 국내 사명은 본문 언급이 실제 소식인 경우가 많고, 해외는 사이드바 매칭이 대부분), 그 안에서 최신순
    body.sort(key=lambda a: (a.region != Region.KR, -a.published_at.timestamp()))
    checking, pending = body[:max_body_checks], body[max_body_checks:]
    no_mention: list[NewsItem] = []
    if checking:
        resolved, _ = resolve_urls(checking, cache, client=client, competitor_mode=True)
        resolved_ids = {id(i) for i in resolved}
        pending += [i for i in checking if id(i) not in resolved_ids]
        for item in resolved:
            try:
                text = fetcher.fetch_text(item.url) if item.url else None
            except Exception as e:  # noqa: BLE001 — 원문 하나 때문에 실행 전체가 멈추면 안 된다
                logger.warning(f"원문 받기 오류 — 다음 실행에서 다시: {e}")
                text = None
            if text is None:
                pending.append(item)
                continue
            excerpt = None
            for k in item.competitor_hits:
                comp = comp_by_key.get(k)
                pat = names_pattern(comp) if comp else None
                if pat and (excerpt := mention_excerpt(text, pat)):
                    break
            if excerpt:
                item.mention, item.excerpt = "body", excerpt
                to_verify.append(item)
            else:
                no_mention.append(item)
        logger.info(f"경쟁사 본문 확인: {len(checking)}건 중 본문 언급 {sum(1 for i in to_verify if i.mention == 'body')}"
                    f" · 이름 없음 {len(no_mention)} · 미룸 {len(pending)}")
    return to_verify, no_mention, pending


def drop_repeated_stories(items: list[NewsItem], flagged: list[NewsItem]) -> list[NewsItem]:
    """이미 보낸 소식과 같다고 표시된 기사를 묶음 단위로 뺀다 (묶음의 절반 이상이 표시되면 묶음 전체)"""
    if not flagged:
        return []
    flagged_ids = {id(i) for i in flagged}
    stories: dict[object, list[NewsItem]] = {}
    for idx, item in enumerate(items):
        stories.setdefault(item.story if item.story is not None else f"solo-{idx}", []).append(item)
    dropped: list[NewsItem] = []
    for members in stories.values():
        hits = sum(1 for m in members if id(m) in flagged_ids)
        if hits * 2 >= len(members):
            dropped.extend(members)
    return dropped


def run_workflow(dry_run: bool = False, hours: Optional[float] = None, use_llm: bool = True,
                 now: Optional[datetime] = None, state: Optional[RunState] = None,
                 client: Optional[GoogleNewsClient] = None, notifier: Optional[SlackNotifier] = None,
                 fetcher: Optional[ArticleFetcher] = None) -> dict:
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
        "competitors": [],
        "main": [],
        "infra": [],
        "notification": False,
        "warnings": [],
    }
    logger.info(f"검색 기간(일): {result['days_by_region']}" + (f" · 게시 {hours}시간 이내" if hours else ""))

    # 1. 검색 (주제 × 언론사 + 경쟁사)
    competitor_days = None
    if config.include_competitors and COMPETITORS:
        competitor_days = (max(1, min(config.max_lookback_days, math.ceil(hours / 24))) if hours is not None
                           else config.competitor_lookback_days)
    search = search_all(now, days_by_region, hours=hours, client=client, competitor_days=competitor_days)
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

    # 3a. 경쟁사 확인 — 경쟁사 검색으로 찾았거나 제목에 경쟁사 이름이 있는 기사
    cache = state.decode_cache()
    comp_candidates = [i for i in candidates if i.competitor_hits] if competitor_days is not None else []
    to_verify, no_mention, body_pending = check_competitor_mentions(
        comp_candidates, cache, client, fetcher or ArticleFetcher(), config.max_body_checks)
    if not to_verify:
        comp = CompetitorOutcome()
    elif use_llm:
        comp = verify_competitors(to_verify, COMPETITORS, threshold=config.competitor_threshold)
    else:
        comp = competitor_fallback(to_verify, COMPETITORS)
    comp.rejected.extend(no_mention)          # 본문에도 이름이 없다 = 사이드바·관련 기사 매칭
    comp.unjudged.extend(body_pending)        # 본문 확인 상한·복원 실패 — 다음 실행에서
    warnings.extend(comp.warnings)
    comp_ids = {id(i) for i in comp_candidates}
    matched_ids = {id(i) for i in comp.matched}
    # 경쟁사 기사로 확인되지 않은 것(아님·미확인) 중 주제 검색으로도 찾은 기사는 주제 판정으로 넘긴다.
    # 미확인(본문 확인 상한·받기 실패)을 묶어 두면 국내 주제 기사(검색 기간 2일)가 영영 판정받지 못할 수 있다
    topic_candidates = [i for i in candidates
                        if id(i) not in comp_ids or (id(i) not in matched_ids and i.query_themes)]
    for item in topic_candidates:
        if id(item) in comp_ids:      # 경쟁사 확인용 발췌는 주제 판정·요약 프롬프트에 섞이지 않게
            item.excerpt, item.mention = "", None
    comp_only_rejected = [i for i in comp.rejected if not i.query_themes]
    competitor_items = sorted(comp.matched, key=lambda a: -a.published_at.timestamp())

    # 3b. 주제 관련성 판정
    if not topic_candidates:
        outcome = TriageOutcome()
    elif use_llm:
        outcome = triage(topic_candidates, threshold=config.relevance_threshold)
    else:
        outcome = keyword_fallback(topic_candidates)
        outcome.warnings.append("AI 판정 없이 제목 키워드 규칙으로 골랐습니다 (--no-llm)")
    warnings.extend(outcome.warnings)

    # 4. 요약 + 같은 소식 묶기 (경쟁사 기사는 따로 — 묶음 번호가 섞이지 않게).
    #    최근 5일에 보낸 소식을 다른 매체가 또 쓴 기사는 빼고 '보냄'으로 기록한다. URL이 필요 없어 복원 전에 한다
    #    (보도자료가 매체 10~18곳에 실려 복원 요청을 크게 줄인다)
    #    경쟁사 섹션을 먼저 정리하고, 주제 기사 요약에는 오늘 경쟁사 섹션에 들어간 기사도 '보낸 기사'로 준다
    #    (같은 보도자료가 제목에 회사명이 있는 사본은 🏁로, 없는 사본은 주제 섹션으로 갈라져 두 번 나오지 않게)
    main_items = list(outcome.main)
    repeats: list[NewsItem] = []
    recent = state.recent_titles(now)

    def _summarize(group: list[NewsItem], context: list[dict], same_competitor: bool) -> list[NewsItem]:
        if not group or not use_llm:
            return []
        flagged: list[NewsItem] = []
        warn = summarize(group, recent=context, repeats=flagged, same_competitor=same_competitor)
        if warn and warn not in warnings:
            warnings.append(warn)
        return drop_repeated_stories(group, flagged)

    comp_repeats = _summarize(competitor_items, recent, True)
    comp_repeat_ids = {id(i) for i in comp_repeats}
    competitor_items = [i for i in competitor_items if id(i) not in comp_repeat_ids]
    # 상한: 경쟁사 최신 N개 소식(묶음 단위 — 보도자료 하나가 매체 10곳에 실려도 1개), 나머지는 이력에 남기지 않아
    # 다음 실행에서. 참고는 최신 8건 (나머지는 탈락 캐시)
    comp_stories = sorted(group_stories(competitor_items), key=lambda g: -g.lead.published_at.timestamp())
    if len(comp_stories) > config.max_competitor_items:
        logger.warning(f"경쟁사 소식 {len(comp_stories)}개 중 최신 {config.max_competitor_items}개만 — 나머지는 다음 실행")
        comp_stories = comp_stories[: config.max_competitor_items]
    competitor_items = [i for g in comp_stories for i in (g.lead, *g.others)]
    today_comp = [{"title": i.title, "outlet": i.outlet_name, "region": i.region.value,
                   "day": i.published_at.astimezone(KST).strftime("%m/%d")} for i in competitor_items]
    main_repeats = _summarize(main_items, recent + today_comp, False)
    main_repeat_ids = {id(i) for i in main_repeats}
    main_items = [i for i in main_items if id(i) not in main_repeat_ids]
    repeats = comp_repeats + main_repeats
    if repeats:
        logger.info(f"이미 보냈거나 경쟁사 섹션에 있는 소식의 다른 매체 기사 {len(repeats)}건 제외")
    result["repeats_skipped"] = len(repeats)

    infra_all = sorted(outcome.infra, key=lambda a: -a.published_at.timestamp()) if config.include_infra else []
    infra = infra_all[: config.max_infra_items]
    infra_ids = {id(i) for i in infra}
    overflow_infra = [i for i in outcome.infra if id(i) not in infra_ids]

    # 5. 원문 URL 복원 (Slack에 링크가 보이는 기사만: 묶음 대표 + 같은 소식 앞 5개) → 원문 URL로 이력·중복 재확인.
    #    '외 N곳'으로만 세는 나머지 사본은 복원하지 않는다 (Google 요청 2회씩 — 보도자료는 사본이 10곳 넘게 나온다)
    def _split_for_decode(items: list[NewsItem]) -> tuple[list[NewsItem], list[NewsItem]]:
        shown, extra = [], []
        for g in group_stories(items):
            shown += [g.lead, *g.others[:MAX_SAME_STORY_LINKS]]
            extra += g.others[MAX_SAME_STORY_LINKS:]
        return shown, extra

    comp_shown, comp_extra = _split_for_decode(competitor_items)
    main_shown, main_extra = _split_for_decode(main_items)
    kept, url_warnings = resolve_urls(comp_shown, cache, client=client, competitor_mode=True)
    kept_topic, topic_warnings = resolve_urls(main_shown + infra, cache, client=client)
    kept += kept_topic + comp_extra + main_extra
    warnings.extend(w for w in url_warnings + topic_warnings if w not in warnings)
    state.update_decoded(cache, now)
    kept = dedupe_resolved([i for i in kept if not state.was_sent(i.history_keys())])
    competitor_ids = {id(i) for i in competitor_items}
    main_ids = {id(i) for i in main_items}
    competitor_items = [i for i in kept if id(i) in competitor_ids]
    main_items = [i for i in kept if id(i) in main_ids]
    infra = [i for i in kept if id(i) in infra_ids]

    result["competitors"] = [_item_summary(i) for i in competitor_items]
    result["main"] = [_item_summary(i) for i in main_items]
    result["infra"] = [_item_summary(i) for i in infra]
    result["warnings"] = warnings
    result["google_requests"] = client.requests

    ctx = DigestContext(run_at=now.astimezone(KST), days_by_region=days_by_region, hours=hours,
                        candidates=len(candidates), warnings=warnings, repeats_skipped=len(repeats))
    messages = build_messages(main_items, infra, ctx, competitors=competitor_items)
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
        cached = outcome.rejected + comp_only_rejected + (overflow_infra if outcome.llm_ok else [])
        for item in cached:
            state.record_rejected(item.title_key, item.score or 0.0, now)
    if sent_ok:
        for idx in delivered:
            for item in messages[idx].items:
                state.mark_sent(item.history_keys(), now)
                state.add_recent(item.title, item.outlet_name, item.region.value,
                                 item.published_at.astimezone(KST).strftime("%m/%d"), now)
        for item in repeats:      # 다른 매체의 같은 소식 — 다시 판정하지 않게 '보냄'으로 기록
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
            print(json.dumps({k: v for k, v in result.items() if k not in ("main", "infra", "competitors")},
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
