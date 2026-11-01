"""FastAPI 应用：健康检查 + 看板 API + 托管前端构建产物。

来自课程 Admin dashboard 页。三个设计点值得注意：
1. `require_login` 用 HTTP Basic，且 `dashboard_password` 为空时**一律拒绝**——
   所以没配密码时接口是关着的，不是敞开的。
2. 前端构建产物挂在 `/`，但注册在最后，所以 `/api/*` 和 `/health` 优先匹配。
3. `POST /api/poll` 让看板上能手动触发一轮轮询，不用等 30 秒。
"""

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db
from app.config import settings
from app.poller import poll_once

app = FastAPI(title="AI customer support")
security = HTTPBasic(auto_error=False)


def require_login(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    valid = (
        credentials is not None
        and settings.dashboard_password != ""
        and secrets.compare_digest(
            credentials.username.encode(), settings.dashboard_username.encode()
        )
        and secrets.compare_digest(
            credentials.password.encode(), settings.dashboard_password.encode()
        )
    )
    if not valid:
        raise HTTPException(status_code=401, detail="Wrong username or password")


class EntryUpdate(BaseModel):
    title: str
    content: str


api = APIRouter(prefix="/api", dependencies=[Depends(require_login)])


@api.get("/login")
def login() -> dict:
    return {"ok": True}


@api.get("/events")
def events() -> list[dict]:
    return db.list_events()


@api.get("/tickets")
def tickets() -> list[dict]:
    return db.list_tickets()


@api.get("/tickets/{ticket_id}/messages")
def ticket_messages(ticket_id: int) -> list[dict]:
    return db.list_messages(ticket_id)


@api.get("/knowledge-base")
def knowledge_base() -> list[dict]:
    return db.list_knowledge_base()


@api.put("/knowledge-base/{entry_id}")
def update_entry(entry_id: int, update: EntryUpdate) -> dict:
    entry = db.update_knowledge_base_entry(entry_id, update.title, update.content)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


@api.post("/poll")
def poll() -> dict:
    return {"messages": poll_once()}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(api)

# 前端构建产物。挂在最后，保证上面的 API 路由优先。
dashboard = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if dashboard.exists():
    app.mount("/", StaticFiles(directory=dashboard, html=True), name="dashboard")
