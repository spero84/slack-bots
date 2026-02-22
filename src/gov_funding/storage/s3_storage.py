"""S3 스냅샷 저장소"""
import json
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from ..utils import get_config, logger
from .models import Announcement, AnnouncementChange, ChangeType, NotificationPayload, Snapshot, Source


class S3Storage:
    """S3 기반 스냅샷 저장소"""

    def __init__(self, bucket: Optional[str] = None, region: Optional[str] = None):
        config = get_config()
        self.bucket = bucket or config.s3_bucket
        self.region = region or config.aws_region
        self.s3_client = boto3.client("s3", region_name=self.region)

    def _get_latest_key(self, source: Source) -> Optional[str]:
        """최신 스냅샷 키 조회"""
        prefix = f"snapshots/{source.value}/"
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=1000,
            )
            if "Contents" not in response:
                return None

            # 가장 최근 파일 반환
            objects = sorted(response["Contents"], key=lambda x: x["LastModified"], reverse=True)
            return objects[0]["Key"] if objects else None
        except ClientError as e:
            logger.error(f"S3 목록 조회 실패: {e}")
            return None

    def get_latest_snapshot(self, source: Source) -> Optional[Snapshot]:
        """최신 스냅샷 조회"""
        key = self._get_latest_key(source)
        if not key:
            logger.info(f"{source.value} 스냅샷 없음")
            return None

        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            data = json.loads(response["Body"].read().decode("utf-8"))
            return Snapshot(**data)
        except ClientError as e:
            logger.error(f"S3 스냅샷 조회 실패: {e}")
            return None
        except Exception as e:
            logger.error(f"스냅샷 파싱 실패: {e}")
            return None

    def save_snapshot(self, snapshot: Snapshot) -> bool:
        """스냅샷 저장"""
        timestamp = snapshot.timestamp.strftime("%Y%m%d_%H%M%S")
        key = f"snapshots/{snapshot.source.value}/{timestamp}.json"

        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=snapshot.model_dump_json(indent=2),
                ContentType="application/json",
            )
            logger.info(f"스냅샷 저장 완료: {key}")
            return True
        except ClientError as e:
            logger.error(f"S3 스냅샷 저장 실패: {e}")
            return False

    def compare_snapshots(
        self,
        current: list[Announcement],
        source: Source,
        deadline_alert_days: int = 7,
    ) -> tuple[Snapshot, NotificationPayload]:
        """스냅샷 비교 및 변경사항 추출

        Args:
            current: 현재 크롤링한 공고 목록
            source: 출처
            deadline_alert_days: 마감 임박 기준일

        Returns:
            (새 스냅샷, 알림 페이로드)
        """
        # 새 스냅샷 생성
        new_snapshot = Snapshot(
            source=source,
            timestamp=datetime.now(),
            announcements=current,
        )

        # 이전 스냅샷 조회
        previous = self.get_latest_snapshot(source)

        payload = NotificationPayload()

        if previous is None:
            # 첫 실행: 모든 공고가 신규
            payload.new_announcements = current
            logger.info(f"첫 실행 - 신규 공고 {len(current)}건")
        else:
            # 이전 공고 ID 매핑
            prev_by_id = {a.id: a for a in previous.announcements}
            prev_ids = set(prev_by_id.keys())
            curr_ids = {a.id for a in current}

            # 신규 공고
            new_ids = curr_ids - prev_ids
            for ann in current:
                if ann.id in new_ids:
                    payload.new_announcements.append(ann)

            # 마감 임박 공고 (기존 공고 중 D-day가 임박한 것)
            for ann in current:
                if ann.id not in new_ids and ann.d_day is not None:
                    if 0 < ann.d_day <= deadline_alert_days:
                        # 이전에 이미 마감 임박이었는지 확인
                        prev_ann = prev_by_id.get(ann.id)
                        if prev_ann and prev_ann.d_day is not None:
                            # 이전에도 임박이었으면 스킵 (중복 알림 방지)
                            if prev_ann.d_day <= deadline_alert_days:
                                continue
                        payload.deadline_soon.append(ann)

            logger.info(
                f"스냅샷 비교 완료 - 신규: {len(payload.new_announcements)}건, "
                f"마감임박: {len(payload.deadline_soon)}건"
            )

        return new_snapshot, payload


def deduplicate_announcements(
    *announcement_lists: list[Announcement],
) -> list[Announcement]:
    """여러 출처의 공고를 병합하고 중복 제거

    인자 순서가 우선순위 (먼저 전달된 출처가 높은 우선순위).
    제목을 정규화하여 중복 체크.

    Args:
        *announcement_lists: 우선순위 순서대로 전달된 공고 목록들

    Returns:
        중복 제거된 공고 목록
    """
    result = []
    seen_titles = set()

    for announcements in announcement_lists:
        for ann in announcements:
            if ann.normalized_title not in seen_titles:
                result.append(ann)
                seen_titles.add(ann.normalized_title)
            else:
                logger.debug(f"중복 제거 ({ann.source.value}): {ann.title}")

    # 소스별 건수 로그
    source_counts = []
    for announcements in announcement_lists:
        if announcements:
            source_name = announcements[0].source.value
            source_counts.append(f"{source_name}: {len(announcements)}건")
    counts_str = ", ".join(source_counts) if source_counts else "없음"
    logger.info(f"중복 제거 완료 - {counts_str} → 총: {len(result)}건")
    return result
