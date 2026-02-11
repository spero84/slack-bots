"""S3 Vectors 기반 AI 뉴스 벡터 저장소"""
import json
import logging

import boto3
from botocore.exceptions import ClientError

from ..utils.config import get_config
from .models import Article

logger = logging.getLogger("ai-news")


class AINewsVectorStorage:
    """S3 Vectors 기반 AI 뉴스 벡터 저장소"""

    INDEX_NAME = "ainewsarticles"
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
                        "nonFilterableMetadataKeys": ["ai_summary", "summary", "url", "authors"],
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

    def upsert_articles(self, articles: list[Article]):
        """기사 목록을 벡터로 변환하여 저장"""
        batch_size = 10
        total = len(articles)

        for i in range(0, total, batch_size):
            batch = articles[i : i + batch_size]
            vectors = []

            for article in batch:
                embed_text = article.title
                if article.ai_summary:
                    embed_text += f"\n{article.ai_summary}"
                elif article.summary:
                    embed_text += f"\n{article.summary}"

                embedding = self.embed_text(embed_text)
                vectors.append({
                    "key": article.vector_key,
                    "data": {"float32": embedding},
                    "metadata": {
                        "source": article.source.value,
                        "category": article.category.value,
                        "title": article.title,
                        "importance_score": article.importance_score or 0.0,
                        "published_at": article.published_at.isoformat() if article.published_at else "",
                        "crawled_at": article.crawled_at.isoformat(),
                        "tags": ",".join(article.tags) if article.tags else "",
                        # non-filterable
                        "ai_summary": article.ai_summary or "",
                        "summary": (article.summary or "")[:500],
                        "url": article.url,
                        "authors": ",".join(article.authors) if article.authors else "",
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
        """기존 벡터 키 목록 조회"""
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

    def search(self, query_text: str, top_k: int = 15, metadata_filter: dict | None = None) -> list[dict]:
        """벡터 유사도 검색"""
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
