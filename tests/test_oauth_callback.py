"""OAuth callback 路由测试：/oauth/feishu/callback 正常/异常流程。"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import store
from feishu_tools import encode_oauth_state
from main import app


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.init_db(tmp_path / "t.db")
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seed_data():
    """创建 agent + binding + principal，返回 (binding_id, principal_id)。"""
    agent = store.create_agent("oauth-test-agent")
    binding = store.create_binding(
        agent["id"], "feishu", {"app_id": "cli_app", "app_secret": "secret"}
    )
    p = store.get_or_create_principal("feishu", "ou_user_001", "Test User")
    return binding["id"], p["id"]


@pytest.fixture
def set_redirect_base(monkeypatch):
    monkeypatch.setenv("TAGMATE_OAUTH_REDIRECT_BASE", "https://example.ngrok.io")


class TestFeishuOAuthCallback:
    def test_missing_code(self, client):
        resp = client.get("/oauth/feishu/callback?state=abc")
        assert resp.status_code == 400
        assert "缺少" in resp.text

    def test_missing_state(self, client):
        resp = client.get("/oauth/feishu/callback?code=abc")
        assert resp.status_code == 400
        assert "缺少" in resp.text

    def test_invalid_state(self, client):
        resp = client.get("/oauth/feishu/callback?code=abc&state=bad!!!")
        assert resp.status_code == 400
        assert "state" in resp.text

    def test_binding_not_found(self, client):
        state = encode_oauth_state("ou_user", "nonexistent_binding")
        resp = client.get(f"/oauth/feishu/callback?code=abc&state={state}")
        assert resp.status_code == 404
        assert "绑定不存在" in resp.text

    @patch("api.exchange_code")
    def test_exchange_code_failure(self, mock_exchange, client, seed_data, set_redirect_base):
        binding_id, principal_id = seed_data
        mock_exchange.side_effect = Exception("network error")
        state = encode_oauth_state(principal_id, binding_id)

        resp = client.get(f"/oauth/feishu/callback?code=authcode&state={state}")
        assert resp.status_code == 502
        assert "换取 token 失败" in resp.text

    @patch("api.exchange_code")
    def test_success(self, mock_exchange, client, seed_data, set_redirect_base):
        binding_id, principal_id = seed_data
        mock_exchange.return_value = {
            "access_token": "at_123",
            "refresh_token": "rt_456",
            "expires_in": 7200,
            "refresh_expires_in": 30 * 86400,
        }
        state = encode_oauth_state(principal_id, binding_id)

        resp = client.get(f"/oauth/feishu/callback?code=authcode&state={state}")
        assert resp.status_code == 200
        assert "授权成功" in resp.text

        # 验证 credentials 已存入
        cred = store.get_credentials(principal_id, "feishu")
        assert cred is not None
        assert cred["access_token"] == "at_123"
        assert cred["refresh_token"] == "rt_456"
