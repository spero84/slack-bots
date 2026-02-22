"""실제 사이트 PDF/DOCX/HWP 파일 추출 통합 테스트

정부 부처 사이트에서 실제 첨부파일을 다운로드하여
텍스트 추출(PDF/DOCX/HWP/HWPX)이 정상 동작하는지 검증.

테스트 대상 크롤러 (requests 기반 우선):
- MSS (중소벤처기업부)
- NIA (한국지능정보사회진흥원)
- Bizinfo (기업마당)
- MSIT (과학기술정보통신부) - Playwright
- MOTIE (산업통상자원부) - Playwright

사용법:
    source /home/ubuntu/venvs/gov-funding/bin/activate
    python scripts/test_file_extraction.py
"""
import asyncio
import sys
import os

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gov_funding.crawlers.mss_crawler import MssCrawler
from src.gov_funding.crawlers.nia_crawler import NiaCrawler
from src.gov_funding.crawlers.bizinfo_crawler import BizinfoCrawler
from src.gov_funding.utils.file_reader import extract_text_from_file

# Playwright 크롤러는 optional import
try:
    from src.gov_funding.crawlers.msit_crawler import MsitCrawler
    MSIT_AVAILABLE = True
except ImportError:
    MSIT_AVAILABLE = False

try:
    from src.gov_funding.crawlers.motie_crawler import MotieCrawler
    MOTIE_AVAILABLE = True
except ImportError:
    MOTIE_AVAILABLE = False

SUPPORTED_EXTENSIONS = {"pdf", "docx", "hwp", "hwpx"}


def get_file_ext(filename: str) -> str:
    """파일 확장자 추출"""
    return filename.lower().rsplit(".", 1)[-1] if "." in filename else ""


async def test_download_and_extract(crawler, attachment: dict) -> dict:
    """첨부파일 다운로드 및 텍스트 추출 테스트"""
    name = attachment.get("name", "")
    url = attachment.get("url", "")
    ext = get_file_ext(name)

    print(f"    - {name} (확장자: {ext})")

    if ext not in SUPPORTED_EXTENSIONS:
        print(f"      → 지원하지 않는 형식, 스킵")
        return {"name": name, "ext": ext, "success": False, "error": "미지원 형식"}

    print(f"      다운로드 중... ({url[:100]})")
    file_bytes = await crawler.download_attachment(url)

    if not file_bytes:
        print(f"      → 다운로드 실패")
        return {"name": name, "ext": ext, "success": False, "error": "다운로드 실패"}

    print(f"      파일 크기: {len(file_bytes):,} bytes")

    # 파일 크기가 너무 작으면 HTML 에러 페이지일 수 있음
    if len(file_bytes) < 500:
        snippet = file_bytes[:200].decode("utf-8", errors="replace")
        if "<html" in snippet.lower() or "<body" in snippet.lower():
            print(f"      → HTML 응답 (에러 페이지), 스킵")
            return {"name": name, "ext": ext, "success": False, "error": "HTML 에러 페이지"}

    try:
        text = extract_text_from_file(file_bytes, name)
        text_len = len(text)
        preview = text[:200].replace("\n", " ") if text else "(빈 텍스트)"

        print(f"      추출된 텍스트: {text_len:,}자")
        print(f"      미리보기: {preview}")

        return {
            "name": name,
            "ext": ext,
            "success": text_len > 0,
            "text_length": text_len,
            "preview": preview,
        }
    except Exception as e:
        print(f"      → 텍스트 추출 오류: {e}")
        return {"name": name, "ext": ext, "success": False, "error": str(e)}


async def test_requests_crawler(crawler_class, name: str) -> dict:
    """requests 기반 크롤러 테스트 (MSS, NIA, Bizinfo)"""
    result = {
        "crawler": name,
        "success": False,
        "crawl_count": 0,
        "detail_found": False,
        "attachments": [],
        "extraction_results": [],
        "message": "",
    }

    print(f"\n{'='*60}")
    print(f"[{name}] 공고 목록 크롤링 (max_items=10)")
    print(f"{'='*60}")

    crawler = crawler_class()
    try:
        announcements = await crawler.crawl(max_items=10)
        result["crawl_count"] = len(announcements)
        print(f"  크롤링 결과: {len(announcements)}건")

        if not announcements:
            result["message"] = "크롤링 결과 없음"
            return result

        for i, ann in enumerate(announcements[:5]):
            print(f"  [{i+1}] {ann.title[:60]}... (ID: {ann.id})")
        if len(announcements) > 5:
            print(f"  ... 외 {len(announcements)-5}건")

        # 각 공고 상세 조회
        for ann in announcements:
            print(f"\n  [상세 조회] ID={ann.id}")
            detail = await crawler.get_detail(ann.id)

            if not detail:
                print(f"    상세 조회 실패")
                continue

            content_len = len(detail.get("content", ""))
            attachments = detail.get("attachments", [])
            print(f"    페이지 내용: {content_len}자, 첨부파일: {len(attachments)}개")

            if content_len > 0:
                result["detail_found"] = True

            if not attachments:
                continue

            result["detail_found"] = True
            for att in attachments:
                ext = get_file_ext(att.get("name", ""))
                if ext not in SUPPORTED_EXTENSIONS:
                    print(f"    - {att.get('name', '')} (확장자: {ext}) → 스킵")
                    continue

                ext_result = await test_download_and_extract(crawler, att)
                result["extraction_results"].append(ext_result)
                result["attachments"].append(att)
                if ext_result.get("success"):
                    result["success"] = True
                    return result

        if not result["success"]:
            supported_found = any(
                get_file_ext(a.get("name", "")) in SUPPORTED_EXTENSIONS
                for a in result["attachments"]
            )
            if not result["detail_found"]:
                result["message"] = "상세 조회 실패 (사이트 변경 또는 접근 불가)"
            elif not result["attachments"] and not supported_found:
                result["message"] = "지원 가능한 첨부파일 없음"
            else:
                result["message"] = "텍스트 추출 실패"
    except Exception as e:
        result["message"] = f"오류: {e}"
        print(f"  오류: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await crawler.close()

    return result


async def test_playwright_crawler(crawler_class, name: str) -> dict:
    """Playwright 기반 크롤러 테스트 (MSIT, MOTIE)

    crawl()과 get_detail() 사이에 브라우저를 재초기화하여
    --single-process 모드에서의 브라우저 종료 문제를 방지.
    """
    result = {
        "crawler": name,
        "success": False,
        "crawl_count": 0,
        "detail_found": False,
        "attachments": [],
        "extraction_results": [],
        "message": "",
    }

    print(f"\n{'='*60}")
    print(f"[{name}] 공고 목록 크롤링 (max_items=5)")
    print(f"{'='*60}")

    # Phase 1: 크롤링 (별도 인스턴스)
    crawler = crawler_class()
    announcement_ids = []
    try:
        announcements = await crawler.crawl(max_items=5)
        result["crawl_count"] = len(announcements)
        print(f"  크롤링 결과: {len(announcements)}건")

        if not announcements:
            result["message"] = "크롤링 결과 없음"
            return result

        for i, ann in enumerate(announcements):
            print(f"  [{i+1}] {ann.title[:60]}... (ID: {ann.id})")
            announcement_ids.append(ann.id)
    except Exception as e:
        result["message"] = f"크롤링 오류: {e}"
        print(f"  크롤링 오류: {e}")
        return result
    finally:
        await crawler.close()

    # Phase 2: 상세 조회 (새 인스턴스로 브라우저 재초기화)
    for ann_id in announcement_ids:
        crawler = crawler_class()
        try:
            print(f"\n  [상세 조회] ID={ann_id}")
            detail = await crawler.get_detail(ann_id)

            if not detail:
                print(f"    상세 조회 실패")
                continue

            content_len = len(detail.get("content", ""))
            attachments = detail.get("attachments", [])
            print(f"    페이지 내용: {content_len}자, 첨부파일: {len(attachments)}개")

            if content_len > 0:
                result["detail_found"] = True

            if not attachments:
                continue

            result["detail_found"] = True
            for att in attachments:
                ext = get_file_ext(att.get("name", ""))
                if ext not in SUPPORTED_EXTENSIONS:
                    print(f"    - {att.get('name', '')} (확장자: {ext}) → 스킵")
                    continue

                ext_result = await test_download_and_extract(crawler, att)
                result["extraction_results"].append(ext_result)
                result["attachments"].append(att)
                if ext_result.get("success"):
                    result["success"] = True
                    return result
        except Exception as e:
            print(f"    오류: {e}")
        finally:
            await crawler.close()

    if not result["success"]:
        if not result["detail_found"]:
            result["message"] = "상세 조회 실패 (사이트 점검 또는 접근 불가)"
        elif not result["attachments"]:
            result["message"] = "지원 가능한 첨부파일 없음"
        else:
            result["message"] = "텍스트 추출 실패"

    return result


async def main():
    """크롤러별 순차 테스트"""
    print("=" * 60)
    print("실제 사이트 PDF/DOCX/HWP 파일 추출 통합 테스트")
    print("=" * 60)

    results = []

    # requests 기반 크롤러 (안정적, 우선 테스트)
    for crawler_class, name in [
        (MssCrawler, "MSS (중소벤처기업부)"),
        (NiaCrawler, "NIA (한국지능정보사회진흥원)"),
        (BizinfoCrawler, "Bizinfo (기업마당)"),
    ]:
        r = await test_requests_crawler(crawler_class, name)
        results.append(r)

    # Playwright 기반 크롤러 (성공하지 못한 경우에만)
    if not any(r["success"] for r in results):
        if MSIT_AVAILABLE:
            results.append(await test_playwright_crawler(MsitCrawler, "MSIT (과학기술정보통신부)"))
        if MOTIE_AVAILABLE and not any(r["success"] for r in results):
            results.append(await test_playwright_crawler(MotieCrawler, "MOTIE (산업통상자원부)"))

    # 결과 요약
    print("\n")
    print("=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)

    total_success = 0
    total_tested = len(results)

    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        print(f"\n  [{status}] {r['crawler']}")
        print(f"    크롤링: {r['crawl_count']}건")
        print(f"    상세조회: {'성공' if r['detail_found'] else '실패'}")
        print(f"    첨부파일: {len(r['attachments'])}개")

        if r["extraction_results"]:
            for er in r["extraction_results"]:
                ext_status = "성공" if er.get("success") else "실패"
                print(f"    추출 [{ext_status}] {er['name'][:50]}: ", end="")
                if er.get("success"):
                    print(f"{er['text_length']:,}자")
                else:
                    print(f"{er.get('error', '알 수 없음')}")
        elif r["message"]:
            print(f"    사유: {r['message']}")

        if r["success"]:
            total_success += 1

    print(f"\n{'='*60}")
    print(f"최종: {total_success}/{total_tested} 크롤러에서 파일 추출 성공")
    print(f"{'='*60}")

    return 0 if total_success > 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
