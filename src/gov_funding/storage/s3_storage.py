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
    kstartup_announcements: list[Announcement],
    bizinfo_announcements: list[Announcement],
    nipa_announcements: list[Announcement],
) -> list[Announcement]:
    """세 출처의 공고를 병합하고 중복 제거

    K-Startup, Bizinfo, NIPA에서 동일한 공고가 올라오는 경우가 있음.
    제목을 정규화하여 중복 체크.
    우선순위: K-Startup > Bizinfo > NIPA

    Args:
        kstartup_announcements: K-Startup 공고
        bizinfo_announcements: 기업마당 공고
        nipa_announcements: NIPA 공고

    Returns:
        중복 제거된 공고 목록
    """
    result = list(kstartup_announcements)
    seen_titles = {a.normalized_title for a in kstartup_announcements}

    for ann in bizinfo_announcements:
        if ann.normalized_title not in seen_titles:
            result.append(ann)
            seen_titles.add(ann.normalized_title)
        else:
            logger.debug(f"중복 제거 (Bizinfo): {ann.title}")

    for ann in nipa_announcements:
        if ann.normalized_title not in seen_titles:
            result.append(ann)
            seen_titles.add(ann.normalized_title)
        else:
            logger.debug(f"중복 제거 (NIPA): {ann.title}")

    logger.info(
        f"중복 제거 완료 - K-Startup: {len(kstartup_announcements)}건, "
        f"Bizinfo: {len(bizinfo_announcements)}건, "
        f"NIPA: {len(nipa_announcements)}건 → 총: {len(result)}건"
    )
    return result
