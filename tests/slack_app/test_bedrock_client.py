"""slack-app Bedrock 임베딩 클라이언트 인증 회귀 테스트

벡터 검색용 임베딩 클라이언트도 만료 가능한 AWS_BEARER_TOKEN_BEDROCK 대신
EC2 IAM Role(SigV4)로 인증해야 한다.
"""
import pytest


def test_embed_client_forces_sigv4(monkeypatch):
    """slack-app 임베딩 클라이언트가 SigV4를 사용하는지"""
    # slack-app은 별도 venv라 다른 서비스 venv에서는 스킵된다
    pytest.importorskip("dotenv")
    pytest.importorskip("slack_bolt")

    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "expired-dummy-token")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")

    from src.slack_app import app as slack_app

    monkeypatch.setattr(slack_app, "_bedrock_embed_client", None)
    assert slack_app._get_bedrock_embed_client().meta.config.signature_version == "v4"
