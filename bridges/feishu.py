"""飞书 Bridge — 通过 WebSocket 长连接收发消息。"""

import json
import os
import threading
import uuid

import lark_oapi as lark
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

from agent import agent
from bridges import Bridge, BridgeStatus

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


# --- Bridge 实现 ---


class FeishuBridge(Bridge):
    def __init__(self):
        super().__init__(name="feishu", bridge_type="feishu")

        self._app_id = os.environ["APP_ID"]
        self._app_secret = os.environ["APP_SECRET"]
        self._seen_msg_ids: set[str] = set()

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

    def start(self) -> None:
        self.info.status = BridgeStatus.CONNECTING

        def _run():
            try:
                from datetime import datetime

                self.info.status = BridgeStatus.CONNECTED
                self.info.connected_at = datetime.now()
                self._ws.start()
            except Exception as e:
                self.info.status = BridgeStatus.ERROR
                self.info.last_error = str(e)

        t = threading.Thread(target=_run, daemon=True, name="feishu-bridge")
        t.start()

    def stop(self) -> None:
        self.info.status = BridgeStatus.DISCONNECTED

    # --- 消息处理 ---

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        msg = data.event.message

        # 去重
        if msg.message_id in self._seen_msg_ids:
            return
        self._seen_msg_ids.add(msg.message_id)
        if len(self._seen_msg_ids) > 10000:
            self._seen_msg_ids.clear()

        # 提取文本
        if msg.message_type == "text":
            user_text = json.loads(msg.content)["text"]
        else:
            user_text = f"[用户发了一条 {msg.message_type} 消息，请告知你目前只支持文本]"

        print(f"[feishu] recv: {user_text}")
        self.info.message_count += 1

        # Step 1: 发 [思考中] 卡片
        card_id = self._create_card(CARD_THINKING)
        if card_id:
            self._send_message(msg, "interactive", json.dumps({"type": "card", "data": {"card_id": card_id}}))

        # Step 2: 调 agent
        result = agent(user_text)
        reply_text = str(result)
        print(f"[feishu] reply: {reply_text[:100]}")

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
