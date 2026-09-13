# TagMate

团队协作 AI 助手：在飞书里 @ bot 对话，管理面建 agent、绑 bot、看日志。设计见 `DESIGN.md`。

## 启动

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # 填 APP_ID / APP_SECRET（飞书应用凭证）
.venv/bin/python scripts/seed_dev.py   # 用 .env 凭证建默认 agent + feishu binding（幂等）
.venv/bin/uvicorn main:app --port 8100
```

打开 http://localhost:8100 进入管理面。数据库默认 `./data/tagmate.db`，可用 `TAGMATE_DB` 覆盖。

## 前端

`frontend/` 是 Vite + Vue 3 工程，构建产物输出到 `static/`（已入库，部署时不需要 node）。

```bash
cd frontend
pnpm install
pnpm dev      # http://localhost:5173，/api 代理到 localhost:8100（需先起后端）
pnpm build    # 产出 static/，改完前端记得 build 并连同 static/ 一起提交
```

## 测试

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
scripts/api_smoke.sh      # API 冒烟（临时库，自清理）
scripts/static_smoke.sh   # 静态托管冒烟（需先 pnpm build）
```
