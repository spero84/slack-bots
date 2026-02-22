#!/usr/bin/env python3
"""Bedrock AI 필터링 테스트"""
import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# AWS 리전 설정
os.environ.setdefault("AWS_REGION", "us-west-2")

from src.analyzers.bedrock_analyzer import BedrockAnalyzer
from src.storage.models import Announcement, Source


async def test_bedrock_analysis():
    """Bedrock 분석 테스트"""
    print("🤖 Bedrock AI 필터링 테스트\n")

    # 테스트용 공고 데이터 (관련성 높음/낮음 혼합)
    test_announcements = [
        Announcement(
            id="high1",
            source=Source.KSTARTUP,
            title="2026년 AI 스타트업 창업지원사업",
            category="IT/SW",
            url="https://example.com/high1",
        ),
        Announcement(
            id="high2",
            source=Source.BIZINFO,
            title="데이터 바우처 지원사업 - 빅데이터 분석 기업 대상",
            category="기술",
            url="https://example.com/high2",
        ),
        Announcement(
            id="low1",
            source=Source.BIZINFO,
            title="농촌 관광자원 활용 지원사업",
            category="관광",
            url="https://example.com/low1",
        ),
        Announcement(
            id="mid1",
            source=Source.KSTARTUP,
            title="청년 창업아카데미 교육생 모집",
            category="교육",
            url="https://example.com/mid1",
        ),
        Announcement(
            id="high3",
            source=Source.KSTARTUP,
            title="SW 개발 스타트업 R&D 지원",
            category="기술",
            url="https://example.com/high3",
        ),
    ]

    print("=" * 60)
    print("📋 테스트 공고 목록")
    print("=" * 60)
    for ann in test_announcements:
        print(f"  - [{ann.id}] {ann.title}")

    print("\n" + "=" * 60)
    print("🔍 Bedrock 분석 시작 (Claude Haiku)")
    print("=" * 60)

    analyzer = BedrockAnalyzer(
        model_id="global.anthropic.claude-opus-4-6-v1",
        region="us-west-2",
    )

    results = []
    for ann in test_announcements:
        print(f"\n분석 중: {ann.title[:40]}...")
        score, summary = await analyzer.analyze_relevance(ann)
        ann.relevance_score = score
        ann.summary = summary
        results.append(ann)

        print(f"  점수: {score:.2f}")
        if summary:
            print(f"  요약: {summary[:60]}...")

    # 결과 정렬 및 필터링
    print("\n" + "=" * 60)
    print("📊 필터링 결과 (임계값: 0.7)")
    print("=" * 60)

    results.sort(key=lambda x: x.relevance_score or 0, reverse=True)

    print("\n✅ 통과 (0.7 이상):")
    for ann in results:
        if ann.relevance_score and ann.relevance_score >= 0.7:
            print(f"  [{ann.relevance_score:.2f}] {ann.title}")

    print("\n❌ 제외 (0.7 미만):")
    for ann in results:
        if not ann.relevance_score or ann.relevance_score < 0.7:
            print(f"  [{ann.relevance_score:.2f}] {ann.title}")


if __name__ == "__main__":
    asyncio.run(test_bedrock_analysis())
