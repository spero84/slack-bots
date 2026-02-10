#!/usr/bin/env python3
"""로컬 테스트 스크립트

크롤러와 필터링을 로컬에서 테스트합니다.
S3, Bedrock 없이 기본 기능만 테스트합니다.
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.crawlers import BizinfoCrawler, KStartupCrawler
from src.analyzers import keyword_filter
from src.storage import deduplicate_announcements


async def test_bizinfo_crawler():
    """Bizinfo 크롤러 테스트"""
    print("\n" + "=" * 50)
    print("🔍 Bizinfo (기업마당) 크롤러 테스트")
    print("=" * 50)

    crawler = BizinfoCrawler()
    try:
        announcements = await crawler.crawl(max_items=20)
        print(f"\n✅ 수집된 공고: {len(announcements)}건\n")

        for i, ann in enumerate(announcements[:5], 1):
            print(f"{i}. {ann.title}")
            print(f"   ID: {ann.id}")
            print(f"   카테고리: {ann.category}")
            print(f"   D-day: {ann.d_day}")
            print(f"   URL: {ann.url}")
            print()

        return announcements
    finally:
        await crawler.close()


async def test_kstartup_crawler():
    """K-Startup 크롤러 테스트"""
    print("\n" + "=" * 50)
    print("🚀 K-Startup 크롤러 테스트")
    print("=" * 50)

    crawler = KStartupCrawler()
    try:
        announcements = await crawler.crawl(max_items=20)
        print(f"\n✅ 수집된 공고: {len(announcements)}건\n")

        for i, ann in enumerate(announcements[:5], 1):
            print(f"{i}. {ann.title}")
            print(f"   ID: {ann.id}")
            print(f"   카테고리: {ann.category}")
            print(f"   D-day: {ann.d_day}")
            print(f"   URL: {ann.url}")
            print()

        return announcements
    except Exception as e:
        print(f"❌ K-Startup 크롤링 오류: {e}")
        print("   (Playwright가 설치되어 있는지 확인하세요)")
        return []
    finally:
        await crawler.close()


async def test_filtering(announcements):
    """키워드 필터링 테스트"""
    print("\n" + "=" * 50)
    print("🎯 키워드 필터링 테스트")
    print("=" * 50)

    filtered = keyword_filter(announcements)
    print(f"\n원본: {len(announcements)}건 → 필터링 후: {len(filtered)}건\n")

    print("필터링된 공고:")
    for i, ann in enumerate(filtered[:10], 1):
        print(f"{i}. {ann.title}")
        print(f"   카테고리: {ann.category}")
        print()

    return filtered


async def main():
    """메인 테스트 함수"""
    print("\n" + "🤖 Gov Funding Monitor 로컬 테스트")
    print(f"   실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Bizinfo 테스트
    bizinfo_announcements = await test_bizinfo_crawler()

    # K-Startup 테스트 (Playwright 필요)
    kstartup_announcements = await test_kstartup_crawler()

    # 중복 제거 테스트
    if bizinfo_announcements or kstartup_announcements:
        print("\n" + "=" * 50)
        print("🔀 중복 제거 테스트")
        print("=" * 50)

        merged = deduplicate_announcements(
            kstartup_announcements,
            bizinfo_announcements,
        )
        print(f"\n병합 결과: {len(merged)}건")

        # 필터링 테스트
        await test_filtering(merged)

    # 결과 저장
    output_dir = project_root / "output"
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"test_result_{timestamp}.json"

    all_announcements = kstartup_announcements + bizinfo_announcements
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            [a.model_dump(mode="json") for a in all_announcements],
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    print(f"\n📁 결과 저장: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
