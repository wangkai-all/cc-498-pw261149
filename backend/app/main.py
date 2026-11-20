"""FastAPI 应用入口。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routers import analytics, corrections, export as export_router, tickets, topics, versions


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="客服工单主题分析系统", version="1.0.0", lifespan=lifespan)

app.include_router(topics.router)
app.include_router(versions.router)
app.include_router(tickets.router)
app.include_router(corrections.router)
app.include_router(analytics.router)
app.include_router(export_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# 前端静态资源
STATIC_DIR = os.environ.get("TICKET_STATIC_DIR", "/workspace/frontend")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
