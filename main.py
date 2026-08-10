"""シーケンス図アノテーションツール（FastAPI エントリポイント）。

起動:
    uvicorn main:app --reload --port 8000
既定では外部通信を行わず、localhost からのみ利用する想定。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database import init_db
from routers import annotations
from runtime import resource_base

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="シーケンス図アノテーションツール", version="1.0.0", lifespan=lifespan)

_origins = os.environ.get("SEQANNO_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(annotations.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ビルド済みフロントエンド（frontend/dist）があれば配信する。
# exe 化時は同梱リソースの基準（resource_base）から探す。
_DIST = resource_base() / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # dist 直下の静的ファイル（favicon・ロゴ等）は実体を返し、
        # それ以外は SPA として index.html にフォールバックする。
        if full_path:
            dist_root = _DIST.resolve()
            candidate = (dist_root / full_path).resolve()
            if candidate.is_file() and dist_root in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
