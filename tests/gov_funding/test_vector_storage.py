"""S3VectorStorage 테스트 (boto3 클라이언트는 가짜)"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from src.gov_funding.storage import Announcement, Source
from src.gov_funding.storage.vector_storage import (
    S3VectorStorage,
    VectorUpsertError,
    announcement_from_metadata,
)


@pytest.fixture
def storage():
    with patch("src.gov_funding.storage.vector_storage.boto3.client", return_value=MagicMock()):
        vs = S3VectorStorage(bucket="test-bucket", region="ap-northeast-2", bedrock_region="us-west-2")
    vs.embed_text = MagicMock(return_value=[0.0] * 1024)
    return vs


def make_ann(i: int) -> Announcement:
    return Announcement(id=str(i), source=Source.NIPA, title=f"공고 {i}", url=f"https://example.com/{i}")


def test_upsert_retries_failed_batch_item_by_item(storage):
    """한 건 때문에 배치가 실패하면 한 건씩 재시도해 나머지는 저장하고, 끝까지 실패한 키만 알린다"""
    calls = []

    def put_vectors(**kwargs):
        keys = [v["key"] for v in kwargs["vectors"]]
        calls.append(keys)
        if "nipa_15" in keys:
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "bad"}}, "PutVectors")

    storage.s3v.put_vectors.side_effect = put_vectors

    with pytest.raises(VectorUpsertError) as exc:
        storage.upsert_announcements([make_ann(i) for i in range(25)])

    assert exc.value.failed_keys == ["nipa_15"]
    # 배치1 + 배치2(실패) + 배치2 개별 10건 + 배치3
    assert len(calls) == 1 + 1 + 10 + 1
    assert calls[-1] == [f"nipa_{i}" for i in range(20, 25)]


def test_upsert_metadata(storage):
    ann = make_ann(1)
    ann.deadline = datetime(2026, 10, 27, 23, 59, 59)
    ann.d_day = 7
    ann.summary = "요약"
    ann.relevance_score = 0.8

    storage.upsert_announcements([ann])

    vector = storage.s3v.put_vectors.call_args.kwargs["vectors"][0]
    assert vector["key"] == "nipa_1"
    assert vector["metadata"]["deadline"] == "2026-10-27T23:59:59"
    assert vector["metadata"]["d_day"] == 7
    assert vector["metadata"]["summary"] == "요약"


def test_get_existing_index_returns_metadata(storage):
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"vectors": [{"key": "nipa_1", "metadata": {"title": "A"}}]},
        {"vectors": [{"key": "nia_2", "metadata": {"title": "B"}}]},
    ]
    storage.s3v.get_paginator.return_value = paginator

    index = storage.get_existing_index()

    assert index == {"nipa_1": {"title": "A"}, "nia_2": {"title": "B"}}
    assert paginator.paginate.call_args.kwargs["returnMetadata"] is True


def test_get_existing_index_raises_on_error(storage):
    """조회 실패를 빈 인덱스로 숨기면 모든 공고가 신규로 재알림된다"""
    paginator = MagicMock()
    paginator.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "ListVectors"
    )
    storage.s3v.get_paginator.return_value = paginator

    with pytest.raises(ClientError):
        storage.get_existing_index()
    with pytest.raises(ClientError):
        storage.get_existing_keys()


class TestAnnouncementFromMetadata:
    def test_round_trip(self):
        meta = {
            "source": "bizinfo",
            "title": "AI 바우처",
            "category": "기술",
            "d_day": 12,
            "department": "중소벤처기업부",
            "organization": "",
            "relevance_score": 0.9,
            "deadline": "2026-11-01T23:59:59",
            "crawled_at": "2026-10-15T00:00:00",
            "summary": "요약",
            "url": "https://www.bizinfo.go.kr/x",
        }

        ann = announcement_from_metadata("bizinfo_PBLN_000000000127352", meta)

        assert ann.id == "PBLN_000000000127352"
        assert ann.source == Source.BIZINFO
        assert ann.deadline == datetime(2026, 11, 1, 23, 59, 59)
        assert ann.d_day == 12
        assert ann.organization is None
        assert ann.crawled_at == datetime(2026, 10, 15)

    def test_no_deadline(self):
        ann = announcement_from_metadata(
            "nia_1", {"source": "nia", "title": "t", "url": "u", "d_day": -1, "deadline": ""}
        )
        assert ann.deadline is None
        assert ann.d_day is None

    @pytest.mark.parametrize("key,meta", [
        ("nia_1", {"source": "unknown", "title": "t", "url": "u"}),
        ("nipa_1", {"source": "nia", "title": "t", "url": "u"}),
        ("nia_1", {"source": "nia", "title": "", "url": "u"}),
    ])
    def test_invalid(self, key, meta):
        assert announcement_from_metadata(key, meta) is None
