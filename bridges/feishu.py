"""飞书 Bridge — 一个 binding 一条 WebSocket 长连接，收消息后组装 InvocationContext 交给 runtime。

lark_oapi.ws.client（1.7.3）用一个模块级 event loop：``Client.start()`` 在其上 run_until_complete
永久阻塞，且 ``_connect`` / ``_receive_message_loop`` 内部都用 ``loop.create_task`` 往这个模块级 loop
上调度任务。因此多个 bridge 各开线程调 start() 会争同一个 loop，也不能每个 bridge 自己 new 一个 loop
（收消息任务会被调度到没人跑的模块 loop 上）。

这里的做法：把 lark 的模块级 loop 放到一个进程内专用线程 run_forever，每个 bridge 用
run_coroutine_threadsafe 在其上跑自己的连接协程（复用 lark 内部 ``_connect`` / ``_ping_loop`` /
``_disconnect``），stop() 时 cancel 该协程并关 ws；最后一个 bridge 停掉时停 loop、线程退出。
"""

import asyncio
import json
import re
import threading
import uuid
from concurrent.futures import Future
from datetime import datetime

import lark_oapi as lark
import lark_oapi.ws.client as _lark_ws
from lark_oapi.core.enum import HttpMethod, AccessTokenType
from lark_oapi.core.model import BaseRequest, BaseResponse
from lark_oapi.api.cardkit.v1 import (
    Card,
    CreateCardRequest,
    CreateCardRequestBody,
    UpdateCardRequest,
    UpdateCardRequestBody,
)
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

import runtime
import store
from bridges import Bridge, BridgeStatus
from context import Principal, Scene, build_context

# 飞书文本里 @ 某人的占位符，如 "@_user_1"
_MENTION_PLACEHOLDER = re.compile(r"@_user_\d+")

# --- 卡片构造 ---


def _card_json(body_md: str) -> str:
    return json.dumps(
        {
            "schema": "2.0",
            "body": {
                "elements": [
                    {"tag": "markdown", "content": body_md},
                ],
            },
        }
    )


CARD_THINKING = _card_json("⏳ *思考中...*")


def _card_done(reply_text: str) -> str:
    return _card_json(reply_text)


# --- 共享 ws event loop 线程（进程内唯一，所有 FeishuBridge 复用；引用计数管理生命周期）---

_loop_lock = threading.Lock()
_loop_thread: threading.Thread | None = None
_loop_users = 0

# stop() 里等 ws 关闭 / 等 loop 线程退出的上限
_STOP_TIMEOUT = 5.0


def ws_loop_thread() -> threading.Thread | None:
    """当前跑 lark ws loop 的线程（无 bridge 在跑时为 None）。供状态检查/测试用。"""
    return _loop_thread


def _acquire_loop() -> asyncio.AbstractEventLoop:
    """引用计数 +1；首个使用者把 lark 模块级 loop 放到专用线程 run_forever。

    lark 在 import 时用 ``asyncio.get_event_loop()`` 取 loop：若 import 发生在某个运行中的 loop
    里（如 FastAPI lifespan 内 lazy import），拿到的就是宿主（uvicorn）的 loop。此时必须换成
    自己的，否则 run_forever 失败、协程被调度到宿主 loop、shutdown 时 stop() 自等死锁。
    lark 各函数按名字引用模块全局 ``loop``，重新赋值模块属性即可生效。
    """
    global _loop_thread, _loop_users
    with _loop_lock:
        _loop_users += 1
        if _loop_thread is None:
            loop = _lark_ws.loop
            if loop.is_running() or loop.is_closed():
                loop = asyncio.new_event_loop()
                _lark_ws.loop = loop
            t = threading.Thread(target=loop.run_forever, daemon=True, name="feishu-ws-loop")
            t.start()
            _loop_thread = t
    return _lark_ws.loop


def _release_loop() -> None:
    """引用计数 -1；最后一个使用者停 loop 并 join 线程。loop 不 close，下次 acquire 可复用。

    join 放在锁内：避免新 acquire 在旧线程尚未退出时又对同一个 loop run_forever。
    """
    global _loop_thread, _loop_users
    with _loop_lock:
        _loop_users -= 1
        if _loop_users > 0 or _loop_thread is None:
            return
        t, _loop_thread = _loop_thread, None
        _lark_ws.loop.call_soon_threadsafe(_lark_ws.loop.stop)
        t.join(_STOP_TIMEOUT)
        if t.is_alive():
            print("[feishu] ws loop thread did not exit within timeout")


# --- Bridge 实现 ---


class FeishuBridge(Bridge):
    def __init__(self, binding: dict):
        super().__init__(binding)

        creds = binding["credentials"]
        self._app_id = creds["app_id"]
        self._app_secret = creds["app_secret"]
        self._seen_msg_ids: set[str] = set()
        self._stopped = False
        # 在共享 loop 上跑 _run() 的句柄；None 表示未启动或已停止
        self._future: Future | None = None

        self._client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .build()
        )

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )

        self._ws = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        # lark 内部重连钩子（公开属性），用来同步状态
        self._ws.on_reconnecting = self._on_reconnecting
        self._ws.on_reconnected = self._on_reconnected

    # --- 生命周期 ---

    def start(self) -> None:
        if self._future is not None:
            return
        self._stopped = False
        self.info.status = BridgeStatus.CONNECTING
        self.info.last_error = None
        loop = _acquire_loop()
        self._future = asyncio.run_coroutine_threadsafe(self._run(), loop)
        self._future.add_done_callback(self._on_run_done)

    def stop(self) -> None:
        self._stopped = True
        fut, self._future = self._future, None
        if fut is None:
            self.info.status = BridgeStatus.DISCONNECTED
            return
        # 先 cancel 主协程（_run），再在 loop 上关 ws 并取消 lark 的收消息 task
        fut.cancel()
        try:
            asyncio.run_coroutine_threadsafe(self._close_ws(), _lark_ws.loop).result(_STOP_TIMEOUT)
        except Exception as e:
            print(f"[feishu:{self.info.binding_id}] close ws failed: {e!r}")
        self.info.status = BridgeStatus.DISCONNECTED
        _release_loop()

    async def _run(self) -> None:
        """对应 lark ``Client.start()``，但跑在共享 loop 上且可 cancel。

        依赖 lark_oapi 1.7.3 私有协程 ``_connect`` / ``_disconnect`` / ``_reconnect`` / ``_ping_loop``。
        """
        ws = self._ws
        try:
            await ws._connect()
        except _lark_ws.ClientException:
            raise
        except Exception as e:
            await ws._disconnect()
            if not ws._auto_reconnect:
                raise
            print(f"[feishu:{self.info.binding_id}] connect failed, reconnecting: {e!r}")
            await ws._reconnect()
        self.info.status = BridgeStatus.CONNECTED
        self.info.connected_at = datetime.now()
        # 拉取机器人/企业元信息（不影响连接状态）
        try:
            meta = await asyncio.to_thread(self._fetch_meta)
            store.update_binding(self.info.binding_id, meta=meta)
            self.info.bot_name = meta.get("bot_name")
            self.info.tenant_name = meta.get("tenant_name")
        except Exception as e:
            print(f"[feishu:{self.info.binding_id}] fetch meta failed: {e!r}")
        await ws._ping_loop()

    def _fetch_meta(self) -> dict:
        """同步拉取机器人信息和企业信息，返回 meta dict。单个接口失败只 log，对应字段 None。"""
        meta: dict = {}

        # --- 机器人信息（GET /open-apis/bot/v3/info，SDK 无 v3 封装，用通用 request）---
        try:
            req = (
                BaseRequest.builder()
                .http_method(HttpMethod.GET)
                .uri("/open-apis/bot/v3/info")
                .token_types({AccessTokenType.TENANT})
                .build()
            )
            resp: BaseResponse = self._client.request(req)
            if resp.success():
                raw_data = json.loads(resp.raw.content)
                bot = raw_data.get("bot", {})
                meta["bot_name"] = bot.get("app_name")
                meta["bot_open_id"] = bot.get("open_id")
                meta["avatar_url"] = bot.get("avatar_url")
            else:
                print(f"[feishu:{self.info.binding_id}] bot/v3/info failed: {resp.code} {resp.msg}")
        except Exception as e:
            print(f"[feishu:{self.info.binding_id}] bot/v3/info error: {e!r}")

        # --- 企业信息（SDK 封装：client.tenant.v2.tenant.query）---
        try:
            from lark_oapi.api.tenant.v2 import QueryTenantRequest
            tenant_req = QueryTenantRequest.builder().build()
            tenant_resp = self._client.tenant.v2.tenant.query(tenant_req)
            if tenant_resp.success():
                t = tenant_resp.data.tenant
                meta["tenant_name"] = t.name
                meta["tenant_key"] = t.tenant_key
            else:
                print(f"[feishu:{self.info.binding_id}] tenant query failed: {tenant_resp.code} {tenant_resp.msg}")
                # 99991672 缺 scope：飞书在 msg 里附带开通链接，透给管理面让用户自己决定
                m = re.search(r"https://open\.feishu\.cn/app/\S+", tenant_resp.msg or "")
                if m:
                    meta["tenant_auth_url"] = m.group(0).rstrip("，。,.")
        except Exception as e:
            print(f"[feishu:{self.info.binding_id}] tenant query error: {e!r}")

        return meta

    async def _close_ws(self) -> None:
        # lark 用 loop.create_task 起的收消息 task 没有句柄可拿，关连接后它会以 ConnectionClosed
        # 结束并打一条 "Task exception was never retrieved"，接受这条日志。
        self._ws._auto_reconnect = False
        await self._ws._disconnect()

    def _on_run_done(self, fut: Future) -> None:
        if fut.cancelled():
            return
        exc = fut.exception()
        if exc is None:
            return
        self.info.status = BridgeStatus.ERROR
        self.info.last_error = str(exc)
        print(f"[feishu:{self.info.binding_id}] ws exited with error: {exc!r}")

    def _on_reconnecting(self) -> None:
        if not self._stopped:
            self.info.status = BridgeStatus.CONNECTING

    def _on_reconnected(self) -> None:
        if not self._stopped:
            self.info.status = BridgeStatus.CONNECTED
            self.info.connected_at = datetime.now()

    # --- 消息处理 ---

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        if self._stopped:
            return
        msg = data.event.message

        # 去重
        if msg.message_id in self._seen_msg_ids:
            return
        self._seen_msg_ids.add(msg.message_id)
        if len(self._seen_msg_ids) > 10000:
            self._seen_msg_ids.clear()

        # 场景判定；群里只处理 @ 了机器人的消息
        if msg.chat_type == "p2p":
            scene: Scene = "dm"
        else:
            if not msg.mentions:
                return
            scene = "thread" if msg.thread_id else "group"

        # 提取文本
        if msg.message_type == "text":
            user_text = json.loads(msg.content)["text"]
            user_text = _MENTION_PLACEHOLDER.sub("", user_text).strip()
        else:
            user_text = f"[用户发了一条 {msg.message_type} 消息，请告知你目前只支持文本]"

        sender = data.event.sender
        principal = Principal(platform="feishu", user_id=sender.sender_id.open_id)
        ctx = build_context(
            agent_id=self.info.agent_id, binding_id=self.info.binding_id, principal=principal,
            scene=scene, chat_id=msg.chat_id, message_id=msg.message_id,
            thread_id=msg.thread_id if scene == "thread" else None,
        )

        print(f"[feishu:{self.info.binding_id}] recv scene={scene} from={principal.user_id}: {user_text}")
        self.info.message_count += 1

        # 不堵住 lark ws 回调线程
        threading.Thread(target=self._handle, args=(msg, ctx, user_text), daemon=True,
                         name=f"feishu-msg-{msg.message_id}").start()

    def _handle(self, msg, ctx, user_text: str) -> None:
        # Step 1: 发 [思考中] 卡片
        card_id = self._create_card(CARD_THINKING)
        if card_id:
            self._send_message(msg, "interactive", json.dumps({"type": "card", "data": {"card_id": card_id}}))

        # Step 2: 调 runtime
        try:
            reply_text = runtime.run_invocation(ctx, user_text)
        except Exception as e:
            print(f"[feishu:{self.info.binding_id}] invocation failed: {e!r}")
            reply_text = f"处理失败：{type(e).__name__}: {e}"
        print(f"[feishu:{self.info.binding_id}] reply: {reply_text[:100]}")

        # Step 3: 更新卡片 or 兜底纯文本
        if card_id and self._update_card(card_id, _card_done(reply_text)):
            return
        self._send_message(msg, "text", json.dumps({"text": reply_text}))

    # --- 飞书 API ---

    def _create_card(self, card_data: str) -> str | None:
        req = (
            CreateCardRequest.builder()
            .request_body(
                CreateCardRequestBody.builder()
                .type("card_json")
                .data(card_data)
                .build()
            )
            .build()
        )
        resp = self._client.cardkit.v1.card.create(req)
        if not resp.success():
            print(f"[feishu] card create failed: {resp.code} {resp.msg}")
            return None
        return resp.data.card_id

    def _update_card(self, card_id: str, card_data: str) -> bool:
        req = (
            UpdateCardRequest.builder()
            .card_id(card_id)
            .request_body(
                UpdateCardRequestBody.builder()
                .card(Card.builder().type("card_json").data(card_data).build())
                .uuid(str(uuid.uuid4()))
                .sequence(1)
                .build()
            )
            .build()
        )
        resp = self._client.cardkit.v1.card.update(req)
        if not resp.success():
            print(f"[feishu] card update failed: {resp.code} {resp.msg}")
            return False
        return True

    def _send_message(self, msg, msg_type: str, content: str) -> bool:
        if msg.chat_type == "p2p":
            req = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(msg.chat_id)
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.create(req)
        else:
            req = (
                ReplyMessageRequest.builder()
                .message_id(msg.message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(content)
                    .msg_type(msg_type)
                    .build()
                )
                .build()
            )
            resp = self._client.im.v1.message.reply(req)

        if not resp.success():
            print(f"[feishu] send failed: {resp.code} {resp.msg}")
            return False
        return True
