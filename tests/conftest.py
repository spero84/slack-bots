"""공통 테스트 설정"""
import pytest


@pytest.fixture(scope="session")
def event_loop_policy():
    """asyncio 이벤트 루프 정책"""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
