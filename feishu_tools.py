"""飞书文档只读工具：OAuth 辅助 + search / read 两个 Strands 工具。"""

import base64
import logging
import os
import time
from urllib.parse import urlencode

import lark_oapi as lark
from lark_oapi.api.authen.v1 import (
    CreateOidcAccessTokenRequest,
    CreateOidcAccessTokenRequestBody,
    CreateOidcRefreshAccessTokenRequest,
    CreateOidcRefreshAccessTokenRequestBody,
)
from lark_oapi.api.docx.v1 import RawContentDocumentRequest
from lark_oapi.api.search.v2 import (
    SearchDocWikiRequest,
    SearchDocWikiRequestBody,
)
from strands import tool

import store
from runtime import NeedAuthorization, get_invocation_ctx, register_tool

log = logging.getLogger(__name__)

FEISHU_OAUTH_SCOPES = ["docx:document:readonly", "search:docs", "offline_access"]
TOKEN_EXPIRY_BUFFER = 30  # seconds


# ---------------------------------------------------------------------------
# lark-oapi client helper
# ---------------------------------------------------------------------------

def _build_client(app_id: str, app_secret: str) -> lark.Client:
    return lark.Client.builder().app_id(app_id).app_secret(app_secret).build()


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def build_authorize_url(app_id: str, redirect_uri: str, scopes: list[str], state: str) -> str:
    """拼飞书 OAuth 授权链接。"""
    params = {
        "client_id": app_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
    }
    return f"https://accounts.feishu.cn/open-apis/authen/v1/authorize?{urlencode(params)}"


def exchange_code(app_id: str, app_secret: str, code: str) -> dict:
    """用授权码换取 access_token（OIDC v1 端点）。"""
    client = _build_client(app_id, app_secret)
    body = (
        CreateOidcAccessTokenRequestBody.builder()
        .grant_type("authorization_code")
        .code(code)
        .build()
    )
    req = CreateOidcAccessTokenRequest.builder().request_body(body).build()
    resp = client.authen.v1.oidc_access_token.create(req)
    if not resp.success():
        raise RuntimeError(f"exchange failed: {resp.code} {resp.msg}")
    d = resp.data
    return {
        "access_token": d.access_token,
        "refresh_token": d.refresh_token,
        "expires_in": d.expires_in,
        "refresh_expires_in": d.refresh_expires_in,
    }


def refresh_access_token(app_id: str, app_secret: str, refresh_token: str) -> dict:
    """用 refresh_token 换新 access_token。"""
    client = _build_client(app_id, app_secret)
    body = (
        CreateOidcRefreshAccessTokenRequestBody.builder()
        .grant_type("refresh_token")
        .refresh_token(refresh_token)
        .build()
    )
    req = CreateOidcRefreshAccessTokenRequest.builder().request_body(body).build()
    resp = client.authen.v1.oidc_refresh_access_token.create(req)
    if not resp.success():
        raise RuntimeError(f"refresh failed: {resp.code} {resp.msg}")
    d = resp.data
    return {
        "access_token": d.access_token,
        "refresh_token": d.refresh_token,
        "expires_in": d.expires_in,
        "refresh_expires_in": d.refresh_expires_in,
    }


# ---------------------------------------------------------------------------
# Token 获取器
# ---------------------------------------------------------------------------

def _make_redirect_uri() -> str:
    base = os.environ.get("TAGMATE_OAUTH_REDIRECT_BASE", "")
    return f"{base}/oauth/feishu/callback"


def encode_oauth_state(principal_id: str, binding_id: str) -> str:
    """state = base64url(principal_id:binding_id)"""
    return base64.urlsafe_b64encode(f"{principal_id}:{binding_id}".encode()).decode()


def decode_oauth_state(state: str) -> tuple[str, str]:
    """返回 (principal_id, binding_id)。"""
    raw = base64.urlsafe_b64decode(state.encode()).decode()
    principal_id, binding_id = raw.split(":", 1)
    return principal_id, binding_id


def _raise_need_auth(app_id: str, redirect_uri: str, principal_id: str, binding_id: str) -> None:
    state = encode_oauth_state(principal_id, binding_id)
    url = build_authorize_url(app_id, redirect_uri, FEISHU_OAUTH_SCOPES, state)
    raise NeedAuthorization(connection="feishu", scopes=FEISHU_OAUTH_SCOPES, authorize_url=url)


def get_user_token(principal_id: str, binding_id: str, binding_credentials: dict) -> str:
    """获取用户 access_token，过期自动刷新，无 token 或 refresh 也过期则抛 NeedAuthorization。"""
    app_id = binding_credentials["app_id"]
    app_secret = binding_credentials["app_secret"]
    redirect_uri = _make_redirect_uri()

    cred = store.get_credentials(principal_id, "feishu")
    if cred is None:
        _raise_need_auth(app_id, redirect_uri, principal_id, binding_id)

    now = time.time()

    # access_token 未过期
    if cred["expires_at"] and cred["expires_at"] > now + TOKEN_EXPIRY_BUFFER:
        return cred["access_token"]

    # 尝试 refresh
    if cred["refresh_token"] and (
        cred["refresh_expires_at"] is None or cred["refresh_expires_at"] > now + TOKEN_EXPIRY_BUFFER
    ):
        try:
            data = refresh_access_token(app_id, app_secret, cred["refresh_token"])
            store.upsert_credentials(
                principal_id=principal_id,
                connection="feishu",
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", cred["refresh_token"]),
                expires_at=now + data.get("expires_in", 7200),
                refresh_expires_at=now + data.get("refresh_expires_in", 30 * 86400),
                scopes=FEISHU_OAUTH_SCOPES,
            )
            return data["access_token"]
        except Exception:
            log.warning("refresh token failed for principal %s, re-auth required", principal_id, exc_info=True)

    _raise_need_auth(app_id, redirect_uri, principal_id, binding_id)


# ---------------------------------------------------------------------------
# Strands 工具
# ---------------------------------------------------------------------------

def _get_token_and_creds() -> tuple[str, dict]:
    """工具内部：从 invocation context 拿 binding credentials，再获取 user token。"""
    ctx = get_invocation_ctx()
    binding = store.get_binding(ctx.binding_id)
    creds = binding["credentials"]
    token = get_user_token(ctx.principal.user_id, ctx.binding_id, creds)
    return token, creds


@tool
def search_feishu_doc(query: str) -> str:
    """搜索飞书文档。返回匹配的文档列表（标题、URL、摘要）。"""
    try:
        token, creds = _get_token_and_creds()
        client = _build_client(creds["app_id"], creds["app_secret"])
        body = SearchDocWikiRequestBody.builder().query(query).page_size(10).build()
        req = SearchDocWikiRequest.builder().request_body(body).build()
        option = lark.RequestOption.builder().user_access_token(token).build()
        resp = client.search.v2.doc_wiki.search(req, option)
    except NeedAuthorization:
        raise
    except Exception as exc:
        log.error("search_feishu_doc failed: %s", exc, exc_info=True)
        return f"搜索失败: {exc}"

    if not resp.success():
        return f"搜索失败: {resp.code} {resp.msg}"

    units = resp.data.res_units if resp.data and resp.data.res_units else []
    if not units:
        return "未找到匹配的文档。"

    lines = []
    for unit in units:
        title = unit.title_highlighted or "(无标题)"
        url = unit.result_meta.url if unit.result_meta else ""
        summary = unit.summary_highlighted or ""
        lines.append(f"- {title}\n  {url}\n  {summary}")
    return "\n".join(lines)


@tool
def read_feishu_doc(document_id: str) -> str:
    """读取飞书文档内容。document_id 可从文档 URL 中提取（如 https://xxx.feishu.cn/docx/XXXXX 中的 XXXXX）。返回文档纯文本内容。"""
    try:
        token, creds = _get_token_and_creds()
        client = _build_client(creds["app_id"], creds["app_secret"])
        req = RawContentDocumentRequest.builder().document_id(document_id).build()
        option = lark.RequestOption.builder().user_access_token(token).build()
        resp = client.docx.v1.document.raw_content(req, option)
    except NeedAuthorization:
        raise
    except Exception as exc:
        log.error("read_feishu_doc failed: %s", exc, exc_info=True)
        return f"读取失败: {exc}"

    if not resp.success():
        return f"读取失败: {resp.code} {resp.msg}"

    return resp.data.content if resp.data and resp.data.content else ""


# ---------------------------------------------------------------------------
# 注册到全局工具表
# ---------------------------------------------------------------------------

register_tool("search_feishu_doc", search_feishu_doc, requires_user_credentials=True)
register_tool("read_feishu_doc", read_feishu_doc, requires_user_credentials=True)
