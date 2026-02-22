"""S3 Vectors 기반 벡터 저장소"""
import json
import logging

import boto3
from botocore.exceptions import ClientError

from ..utils import get_config
from .models import Announcement

logger = logging.getLogger("gov-funding-monitor")


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
        self.s3v = boto3.client("s3vectors", region_name=self.region)
        self.bedrock = boto3.client("bedrock-runtime", region_name=self.bedrock_region)

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
        """공고 목록을 벡터로 변환하여 저장 (배치)"""
        batch_size = 10
        total = len(announcements)

        for i in range(0, total, batch_size):
            batch = announcements[i : i + batch_size]
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
            logger.info(f"벡터 저장: {i + len(batch)}/{total}건")

        logger.info(f"벡터 저장 완료: 총 {total}건")

    def get_existing_keys(self) -> set[str]:
        """인덱스의 기존 벡터 키 목록 조회"""
        keys = set()
        try:
            paginator = self.s3v.get_paginator("list_vectors")
            for page in paginator.paginate(
                vectorBucketName=self.bucket,
                indexName=self.INDEX_NAME,
            ):
                for v in page.get("vectors", []):
                    keys.add(v["key"])
        except ClientError as e:
            logger.error(f"벡터 키 목록 조회 실패: {e}")
        return keys

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
