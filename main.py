"""TodenYOLO（FastAPI エントリポイント）。

起動:
    uvicorn main:app --reload --port 8000
既定では外部通信を行わず、localhost からのみ利用する想定。
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from database import init_db
from routers import annotations, ml
from runtime import resource_base

_DESKTOP_MODE = os.environ.get("SEQANNO_DESKTOP") == "1"
_DESKTOP_IDLE_TIMEOUT_SECONDS = max(
    30, int(os.environ.get("SEQANNO_DESKTOP_IDLE_TIMEOUT_SECONDS", "300"))
)
_desktop_last_heartbeat = time.monotonic()
_desktop_watchdog_started = False
_desktop_lock = threading.Lock()


def _touch_desktop_heartbeat() -> None:
    global _desktop_last_heartbeat
    with _desktop_lock:
        _desktop_last_heartbeat = time.monotonic()


def _shutdown_process_later(delay_seconds: float = 0.5) -> None:
    timer = threading.Timer(delay_seconds, lambda: os._exit(0))
    timer.daemon = True
    timer.start()


def _start_desktop_watchdog() -> None:
    global _desktop_watchdog_started
    with _desktop_lock:
        if _desktop_watchdog_started:
            return
        _desktop_watchdog_started = True

    def watch() -> None:
        while True:
            time.sleep(min(15, max(5, _DESKTOP_IDLE_TIMEOUT_SECONDS / 3)))
            with _desktop_lock:
                idle_seconds = time.monotonic() - _desktop_last_heartbeat
            if idle_seconds >= _DESKTOP_IDLE_TIMEOUT_SECONDS:
                os._exit(0)

    thread = threading.Thread(target=watch, name="desktop-idle-watchdog", daemon=True)
    thread.start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if _DESKTOP_MODE:
        _touch_desktop_heartbeat()
        _start_desktop_watchdog()
    yield


app = FastAPI(title="TodenYOLO", version="1.0.0", lifespan=lifespan)

_origins = os.environ.get("SEQANNO_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(annotations.router)
app.include_router(ml.router)


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


@app.middleware("http")
async def _reject_foreign_origins(request: Request, call_next):
    """localhost CSRF 対策: Origin がローカル以外のブラウザ発リクエストを拒否する。

    この API は認証を持たないため、multipart などプリフライト不要の POST は
    任意サイトから送信できてしまう（アプリ終了・モデル登録など）。
    Origin 未送信（curl・ファイル配信でない同一オリジン環境）は通し、
    送信される場合はループバック系ホストのみ許可する。
    """
    origin = request.headers.get("origin")
    if origin:
        try:
            host = (urlsplit(origin).hostname or "").lower()
        except ValueError:
            host = ""
        if host not in _LOOPBACK_HOSTS and not host.endswith(".localhost"):
            return JSONResponse({"detail": "許可されていないオリジンです"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health() -> dict:
    if _DESKTOP_MODE:
        _touch_desktop_heartbeat()
    return {"status": "ok", "desktop": _DESKTOP_MODE}


@app.post("/api/desktop/heartbeat")
def desktop_heartbeat() -> dict:
    if not _DESKTOP_MODE:
        raise HTTPException(404, "デスクトップ実行時のみ利用できます")
    _touch_desktop_heartbeat()
    return {"status": "ok"}


@app.post("/api/desktop/shutdown")
def desktop_shutdown() -> dict:
    if not _DESKTOP_MODE:
        raise HTTPException(404, "デスクトップ実行時のみ利用できます")
    _shutdown_process_later()
    return {"status": "shutting_down"}


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
