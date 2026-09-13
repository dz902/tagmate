import pytest

import runtime
from context import InvocationContext, Principal, build_context, make_session_key

P = Principal("feishu", "ou_1", display_name="张三")


def test_session_key_dm():
    assert make_session_key("a1", P, "dm", "oc_x") == "a1:dm:feishu:ou_1"


def test_session_key_group():
    assert make_session_key("a1", P, "group", "oc_x") == "a1:chat:oc_x"


def test_session_key_thread():
    assert make_session_key("a1", P, "thread", "oc_x", "omt_1") == "a1:chat:oc_x:omt_1"
    with pytest.raises(ValueError):
        make_session_key("a1", P, "thread", "oc_x", None)


def test_build_context_fills_session_key():
    ctx = build_context("a1", "b1", P, "group", "oc_x", "om_1")
    assert isinstance(ctx, InvocationContext)
    assert ctx.session_key == "a1:chat:oc_x"
    assert ctx.thread_id is None


@pytest.fixture
def fake_cred_tool():
    def secret_tool():
        return "secret"

    runtime.register_tool("secret_tool", secret_tool, requires_user_credentials=True)
    yield secret_tool
    del runtime.TOOL_REGISTRY["secret_tool"]


def test_select_tools_filters_by_scene(fake_cred_tool):
    tools = ["current_time", "secret_tool", "not_registered"]
    assert runtime.select_tools(tools, "dm") == [runtime.current_time, fake_cred_tool]
    assert runtime.select_tools(tools, "group") == [runtime.current_time]
    assert runtime.select_tools(tools, "thread") == [runtime.current_time]


def test_system_prompt_mentions_scene_and_speaker():
    dm = runtime.build_system_prompt("指令", build_context("a", "b", P, "dm", "c", "m"))
    assert "指令" in dm and "张三" in dm and "全群可见" not in dm
    group = runtime.build_system_prompt("指令", build_context("a", "b", P, "group", "c", "m"))
    assert "全群可见" in group
