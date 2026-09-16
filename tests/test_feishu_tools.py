"""飞书工具测试：OAuth helpers、token 获取、search/read 工具函数。全部 mock，不调真实飞书 API。"""

import time
from unittest.mock import MagicMock, patch

import pytest

import store
from context import InvocationContext, Principal
from feishu_tools import (
    FEISHU_OAUTH_SCOPES,
    build_authorize_url,
    decode_oauth_state,
    encode_oauth_state,
    get_user_token,
    read_feishu_doc,
    search_feishu_doc,
)
from runtime import NeedAuthorization, _invocation_ctx


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.init_db(tmp_path / "t.db")
    yield


@pytest.fixture
def binding_creds():
    return {"app_id": "cli_test123", "app_secret": "secret456"}


@pytest.fixture
def principal_id():
    p = store.get_or_create_principal("feishu", "ou_user_abc", "Test User")
    return p["id"]


@pytest.fixture
def binding_id(binding_creds):
    agent = store.create_agent("test-agent")
    b = store.create_binding(agent["id"], "feishu", binding_creds)
    return b["id"]


@pytest.fixture
def set_redirect_base(monkeypatch):
    monkeypatch.setenv("TAGMATE_OAUTH_REDIRECT_BASE", "https://example.ngrok.io")


# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------

class TestBuildAuthorizeUrl:
    def test_url_format(self):
        url = build_authorize_url("cli_app", "https://x.io/cb", ["scope_a", "scope_b"], "st123")
        assert url.startswith("https://accounts.feishu.cn/open-apis/authen/v1/authorize?")
        assert "client_id=cli_app" in url
        assert "response_type=code" in url
        assert "redirect_uri=https" in url
        assert "scope=scope_a+scope_b" in url
        assert "state=st123" in url


# ---------------------------------------------------------------------------
# encode / decode oauth state
# ---------------------------------------------------------------------------

class TestOAuthState:
    def test_roundtrip(self):
        state = encode_oauth_state("p123", "b456")
        pid, bid = decode_oauth_state(state)
        assert pid == "p123"
        assert bid == "b456"

    def test_roundtrip_with_colons(self):
        """binding_id 里带冒号也能 split(1) 正确拆。"""
        state = encode_oauth_state("p1", "b:2:3")
        pid, bid = decode_oauth_state(state)
        assert pid == "p1"
        assert bid == "b:2:3"

    def test_invalid_state_raises(self):
        with pytest.raises(Exception):
            decode_oauth_state("not-valid-base64!!!")


# ---------------------------------------------------------------------------
# get_user_token
# ---------------------------------------------------------------------------

class TestGetUserToken:
    def test_no_credentials_raises(self, principal_id, binding_id, binding_creds, set_redirect_base):
        with pytest.raises(NeedAuthorization) as exc_info:
            get_user_token(principal_id, binding_id, binding_creds)
        assert exc_info.value.connection == "feishu"
        assert "authorize" in exc_info.value.authorize_url

    def test_valid_token_returned(self, principal_id, binding_id, binding_creds, set_redirect_base):
        store.upsert_credentials(
            principal_id=principal_id,
            connection="feishu",
            access_token="good_token",
            expires_at=time.time() + 3600,
        )
        token = get_user_token(principal_id, binding_id, binding_creds)
        assert token == "good_token"

    @patch("feishu_tools.refresh_access_token")
    def test_expired_access_refreshes(self, mock_refresh, principal_id, binding_id, binding_creds, set_redirect_base):
        mock_refresh.return_value = {
            "access_token": "new_token",
            "refresh_token": "new_refresh",
            "expires_in": 7200,
            "refresh_expires_in": 30 * 86400,
        }
        store.upsert_credentials(
            principal_id=principal_id,
            connection="feishu",
            access_token="old_token",
            refresh_token="valid_refresh",
            expires_at=time.time() - 100,  # access 过期
            refresh_expires_at=time.time() + 86400,  # refresh 有效
        )
        token = get_user_token(principal_id, binding_id, binding_creds)
        assert token == "new_token"
        mock_refresh.assert_called_once_with("cli_test123", "secret456", "valid_refresh")
        # 验证 store 里也更新了
        cred = store.get_credentials(principal_id, "feishu")
        assert cred["access_token"] == "new_token"

    def test_both_expired_raises(self, principal_id, binding_id, binding_creds, set_redirect_base):
        store.upsert_credentials(
            principal_id=principal_id,
            connection="feishu",
            access_token="old",
            refresh_token="old_refresh",
            expires_at=time.time() - 100,
            refresh_expires_at=time.time() - 100,
        )
        with pytest.raises(NeedAuthorization):
            get_user_token(principal_id, binding_id, binding_creds)


# ---------------------------------------------------------------------------
# search_feishu_doc / read_feishu_doc（Strands @tool — 直接调用返回原始字符串）
# ---------------------------------------------------------------------------

def _make_invocation_ctx(principal_id, binding_id):
    return InvocationContext(
        agent_id="agent_1",
        binding_id=binding_id,
        principal=Principal(platform="feishu", user_id=principal_id),
        scene="dm",
        chat_id="chat_1",
        message_id="msg_1",
        thread_id=None,
        session_key="k",
    )


class TestSearchFeishuDoc:
    @patch("feishu_tools._build_client")
    @patch("feishu_tools._get_token_and_creds")
    def test_success(self, mock_get, mock_build, principal_id, binding_id, binding_creds, set_redirect_base):
        mock_get.return_value = ("tok", binding_creds)
        # Build mock lark response with res_units
        unit_a = MagicMock(title_highlighted="Doc A", summary_highlighted="summary A")
        unit_a.result_meta.url = "https://a"
        unit_b = MagicMock(title_highlighted="Doc B", summary_highlighted="summary B")
        unit_b.result_meta.url = "https://b"
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.res_units = [unit_a, unit_b]
        mock_build.return_value.search.v2.doc_wiki.search.return_value = mock_resp

        ctx = _make_invocation_ctx(principal_id, binding_id)
        token = _invocation_ctx.set(ctx)
        try:
            result = search_feishu_doc(query="test")
        finally:
            _invocation_ctx.reset(token)

        assert "Doc A" in result
        assert "Doc B" in result

    @patch("feishu_tools._build_client")
    @patch("feishu_tools._get_token_and_creds")
    def test_empty_results(self, mock_get, mock_build, principal_id, binding_id, binding_creds, set_redirect_base):
        mock_get.return_value = ("tok", binding_creds)
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.res_units = []
        mock_build.return_value.search.v2.doc_wiki.search.return_value = mock_resp

        ctx = _make_invocation_ctx(principal_id, binding_id)
        token = _invocation_ctx.set(ctx)
        try:
            result = search_feishu_doc(query="nothing")
        finally:
            _invocation_ctx.reset(token)

        assert "未找到" in result

    @patch("feishu_tools._build_client")
    @patch("feishu_tools._get_token_and_creds")
    def test_api_error_returns_text(self, mock_get, mock_build, principal_id, binding_id, binding_creds, set_redirect_base):
        mock_get.return_value = ("tok", binding_creds)
        mock_build.side_effect = Exception("timeout")

        ctx = _make_invocation_ctx(principal_id, binding_id)
        token = _invocation_ctx.set(ctx)
        try:
            result = search_feishu_doc(query="fail")
        finally:
            _invocation_ctx.reset(token)

        assert "搜索失败" in result


class TestReadFeishuDoc:
    @patch("feishu_tools._build_client")
    @patch("feishu_tools._get_token_and_creds")
    def test_success(self, mock_get, mock_build, principal_id, binding_id, binding_creds, set_redirect_base):
        mock_get.return_value = ("tok", binding_creds)
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.content = "Hello document content"
        mock_build.return_value.docx.v1.document.raw_content.return_value = mock_resp

        ctx = _make_invocation_ctx(principal_id, binding_id)
        token = _invocation_ctx.set(ctx)
        try:
            result = read_feishu_doc(document_id="docx123")
        finally:
            _invocation_ctx.reset(token)

        assert "Hello document content" in result

    @patch("feishu_tools._build_client")
    @patch("feishu_tools._get_token_and_creds")
    def test_api_error_returns_text(self, mock_get, mock_build, principal_id, binding_id, binding_creds, set_redirect_base):
        mock_get.return_value = ("tok", binding_creds)
        mock_build.side_effect = Exception("server error")

        ctx = _make_invocation_ctx(principal_id, binding_id)
        token = _invocation_ctx.set(ctx)
        try:
            result = read_feishu_doc(document_id="docx123")
        finally:
            _invocation_ctx.reset(token)

        assert "读取失败" in result
