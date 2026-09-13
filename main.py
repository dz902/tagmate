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
import store
from api import router as api_router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    infos = bridges.start_all_enabled()
    print(f"[tagmate] started {len(infos)} bridge(s)")
    yield
    bridges.stop_all()


app = FastAPI(title="TagMate", lifespan=lifespan)
app.include_router(api_router)


# --- 前端（frontend/ 经 vite build 产出到 static/）---

ASSETS_DIR = STATIC_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8100")))
