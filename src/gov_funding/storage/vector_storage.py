"""S3 Vectors 기반 벡터 저장소"""
import json
import logging
from datetime import datetime
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..utils import get_config
from .models import Announcement, Source

logger = logging.getLogger("gov-funding-monitor")

# 일시적 스로틀링/네트워크 오류는 botocore가 재시도
RETRY_CONFIG = {"max_attempts": 5, "mode": "adaptive"}


class VectorUpsertError(RuntimeError):
    """일부 배치 저장 실패 (failed_keys: 저장하지 못한 키)"""

    def __init__(self, failed_keys: list[str]):
        self.failed_keys = failed_keys
        super().__init__(f"벡터 저장 실패 {len(failed_keys)}건: {', '.join(failed_keys[:5])}")


class S3VectorStorage:
    """S3 Vectors 기반 공고 벡터 저장소

    - 공고를 벡터 임베딩 + 메타데이터로 저장
    - 벡터 유사도 검색 + 메타데이터 필터링 지원
    """

    INDEX_NAME = "announcements"
    DIMENSION = 1024
    EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"

    def __init__(self, bucket=None, region=None, bedrock_region=None):
        config = get_config()
        self.bucket = bucket or config.s3_bucket
        self.region = region or config.aws_region
        self.bedrock_region = bedrock_region or config.bedrock_region
        self.s3v = boto3.client("s3vectors", region_name=self.region, config=Config(retries=RETRY_CONFIG))
        # signature_version="v4" 명시 — AWS_BEARER_TOKEN_BEDROCK(Claude CLI용, 만료 가능)을
        # 무시하고 EC2 IAM Role(SigV4)로 인증한다.
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=self.bedrock_region,
            config=Config(signature_version="v4", retries=RETRY_CONFIG),
        )

    def ensure_index(self):
        """인덱스가 없으면 자동 생성"""
        try:
            self.s3v.get_index(
                vectorBucketName=self.bucket,
                indexName=self.INDEX_NAME,
            )
            logger.info(f"벡터 인덱스 확인 완료: {self.INDEX_NAME}")
        except ClientError as e:
            if e.response["Error"]["Code"] == "NotFoundException":
                logger.info(f"벡터 인덱스 생성 중: {self.INDEX_NAME}")
                self.s3v.create_index(
                    vectorBucketName=self.bucket,
                    indexName=self.INDEX_NAME,
                    dimension=self.DIMENSION,
                    distanceMetric="cosine",
                    dataType="float32",
                    metadataConfiguration={
                        "nonFilterableMetadataKeys": ["summary", "url"],
                    },
                )
                logger.info(f"벡터 인덱스 생성 완료: {self.INDEX_NAME}")
            else:
                raise

    def embed_text(self, text: str) -> list[float]:
        """Titan Text Embeddings V2로 텍스트 임베딩 생성"""
        resp = self.bedrock.invoke_model(
            modelId=self.EMBEDDING_MODEL,
            body=json.dumps({"inputText": text, "dimensions": self.DIMENSION}),
        )
        body = json.loads(resp["body"].read())
        return body["embedding"]

    def upsert_announcements(self, announcements: list[Announcement]):
        """공고 목록을 벡터로 변환하여 저장 (배치)

        한 배치가 실패해도 나머지 배치는 계속 저장하고, 끝에서 실패한 키를 VectorUpsertError로 알린다.
        """
        batch_size = 10
        total = len(announcements)
        failed_keys: list[str] = []

        for i in range(0, total, batch_size):
            batch = announcements[i : i + batch_size]
            try:
                self._put_batch(batch)
                logger.info(f"벡터 저장: {i + len(batch)}/{total}건")
            except Exception as e:
                # 한 건 때문에 배치 전체가 실패할 수 있으므로 한 건씩 다시 시도
                logger.warning(f"벡터 배치 저장 실패 ({i + 1}~{i + len(batch)}/{total}건) - 개별 재시도: {e}")
                for ann in batch:
                    try:
                        self._put_batch([ann])
                    except Exception as item_error:
                        failed_keys.append(f"{ann.source.value}_{ann.id}")
                        logger.error(f"벡터 저장 실패 ({ann.source.value}_{ann.id}): {item_error}")

        if failed_keys:
            raise VectorUpsertError(failed_keys)
        logger.info(f"벡터 저장 완료: 총 {total}건")

    def _put_batch(self, batch: list[Announcement]):
        """배치 1개 임베딩 + 저장"""
        vectors = []
        for ann in batch:
            # 제목 + 요약을 임베딩 텍스트로 사용
            embed_text = ann.title
            if ann.summary:
                embed_text += f"\n{ann.summary}"

            embedding = self.embed_text(embed_text)
            vectors.append({
                "key": f"{ann.source.value}_{ann.id}",
                "data": {"float32": embedding},
                "metadata": {
                    # filterable metadata
                    "source": ann.source.value,
                    "title": ann.title,
                    "category": ann.category or "",
                    "d_day": ann.d_day if ann.d_day is not None else -1,
                    "department": ann.department or "",
                    "organization": ann.organization or "",
                    "relevance_score": ann.relevance_score or 0.0,
                    "deadline": ann.deadline.isoformat() if ann.deadline else "",
                    "crawled_at": ann.crawled_at.isoformat(),
                    # non-filterable metadata
                    "summary": ann.summary or "",
                    "url": ann.url,
                },
            })

        self.s3v.put_vectors(
            vectorBucketName=self.bucket,
            indexName=self.INDEX_NAME,
            vectors=vectors,
        )

    def get_existing_keys(self) -> set[str]:
        """인덱스의 기존 벡터 키 목록 조회

        조회 실패 시 예외를 그대로 올린다. 빈 집합을 돌려주면 '저장된 공고 없음'과 구분되지 않아
        모든 공고가 신규로 재알림된다.
        """
        keys = set()
        paginator = self.s3v.get_paginator("list_vectors")
        for page in paginator.paginate(
            vectorBucketName=self.bucket,
            indexName=self.INDEX_NAME,
        ):
            for v in page.get("vectors", []):
                keys.add(v["key"])
        return keys

    def get_existing_index(self) -> dict[str, dict]:
        """인덱스의 전체 벡터 키 → 메타데이터 조회 (요약 포함)

        조회 실패 시 예외를 그대로 올린다 (get_existing_keys와 같은 이유).
        """
        index = {}
        paginator = self.s3v.get_paginator("list_vectors")
        for page in paginator.paginate(
            vectorBucketName=self.bucket,
            indexName=self.INDEX_NAME,
            returnMetadata=True,
        ):
            for v in page.get("vectors", []):
                index[v["key"]] = v.get("metadata", {})
        return index

    def get_vectors_metadata(self, keys: list[str]) -> dict[str, dict]:
        """특정 키들의 메타데이터 조회 (배치)"""
        result = {}
        batch_size = 10

        for i in range(0, len(keys), batch_size):
            batch = keys[i : i + batch_size]
            try:
                resp = self.s3v.get_vectors(
                    vectorBucketName=self.bucket,
                    indexName=self.INDEX_NAME,
                    keys=batch,
                    returnMetadata=True,
                )
                for v in resp.get("vectors", []):
                    result[v["key"]] = v.get("metadata", {})
            except ClientError as e:
                logger.error(f"벡터 메타데이터 조회 실패: {e}")

        return result

    def search(self, query_text: str, top_k: int = 15, metadata_filter: dict | None = None) -> list[dict]:
        """벡터 유사도 검색 + 메타데이터 필터"""
        embedding = self.embed_text(query_text)

        kwargs = {
            "vectorBucketName": self.bucket,
            "indexName": self.INDEX_NAME,
            "queryVector": {"float32": embedding},
            "topK": top_k,
            "returnMetadata": True,
            "returnDistance": True,
        }
        if metadata_filter:
            kwargs["filter"] = metadata_filter

        try:
            resp = self.s3v.query_vectors(**kwargs)
            return resp.get("vectors", [])
        except ClientError as e:
            logger.error(f"벡터 검색 실패: {e}")
            return []


def announcement_from_metadata(key: str, metadata: dict) -> Optional[Announcement]:
    """저장된 벡터 메타데이터로 Announcement 복원 (오늘 크롤링되지 않은 공고의 마감 리마인더용)"""
    try:
        source = Source(metadata["source"])
    except (KeyError, ValueError):
        return None

    prefix = f"{source.value}_"
    if not key.startswith(prefix) or not metadata.get("title") or not metadata.get("url"):
        return None

    def parse_dt(value: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(value) if value else None
        except ValueError:
            return None

    d_day = metadata.get("d_day")
    return Announcement(
        id=key[len(prefix):],
        source=source,
        title=metadata["title"],
        category=metadata.get("category") or None,
        deadline=parse_dt(metadata.get("deadline", "")),
        d_day=int(d_day) if isinstance(d_day, (int, float)) and d_day >= 0 else None,
        department=metadata.get("department") or None,
        organization=metadata.get("organization") or None,
        url=metadata["url"],
        summary=metadata.get("summary") or None,
        relevance_score=metadata.get("relevance_score"),
        crawled_at=parse_dt(metadata.get("crawled_at", "")) or datetime.now(),
    )
