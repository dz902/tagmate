"""TagMate — FastAPI 主入口。"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import bridges
from bridges.feishu import FeishuBridge

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动所有 bridges
    if os.environ.get("APP_ID"):
        feishu = FeishuBridge()
        bridges.register(feishu)
        feishu.start()
        print("[tagmate] feishu bridge started")

    yield
    # shutdown: 停止所有 bridges
    for info in bridges.get_all():
        b = bridges.get(info.name)
        if b:
            b.stop()


app = FastAPI(title="TagMate", lifespan=lifespan)


# --- API ---


@app.get("/api/bridges")
def list_bridges():
    return [info.to_dict() for info in bridges.get_all()]


@app.get("/api/status")
def status():
    return {"status": "ok", "bridges": len(bridges.get_all())}


# --- 前端 ---

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8100")))
