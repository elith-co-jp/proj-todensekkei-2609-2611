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
from routers import annotations, attributes, circuit, drawing_sets, exchange, ml, tables
from runtime import resource_base
from services import analysis_jobs

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
            # 裏で解析中（DD-0039）なら、画面を閉じていても終わるまで待つ
            if idle_seconds >= _DESKTOP_IDLE_TIMEOUT_SECONDS and not analysis_jobs.running():
                os._exit(0)

    thread = threading.Thread(target=watch, name="desktop-idle-watchdog", daemon=True)
    thread.start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    # 前回のプロセスで学習中のまま残った学習ジョブを失敗にする（DD-0047。スレッドは再起動で消えている）
    from services.training_service import recover_interrupted_runs

    recover_interrupted_runs()
    # 同じ画素の OCR は前の結果を使う（解析のやり直しを速くする。DD-0056）。置き場はデータの下
    from analysis import ocr_cache
    from database import DATA_DIR

    ocr_cache.configure(DATA_DIR / "ocr_cache")
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
app.include_router(circuit.router)
app.include_router(attributes.router)
app.include_router(exchange.router)
app.include_router(drawing_sets.router)
app.include_router(tables.router)


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# 運用者が SEQANNO_CORS_ORIGINS で明示したオリジンも信頼する
_CONFIGURED_ORIGINS = {o.strip().rstrip("/").lower() for o in _origins.split(",") if o.strip()}


# DNS リバインディング対策で受け付ける Host。ループバックと許可オリジンのホストに加え、
# SEQANNO_ALLOWED_HOSTS（カンマ区切り）で足せる（社内サーバで動かすときなど）
_ALLOWED_HOSTS = (
    _LOOPBACK_HOSTS
    | {(urlsplit(o).hostname or "").lower() for o in _CONFIGURED_ORIGINS}
    | {h.strip().lower() for h in os.environ.get("SEQANNO_ALLOWED_HOSTS", "").split(",") if h.strip()}
) - {""}

# 画面を他サイトの iframe に埋め込ませない（クリックジャッキング対策）など、全応答に付ける守りのヘッダ
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


def _host_name(host_header: str) -> str:
    """Host ヘッダ（例: 127.0.0.1:8010、[::1]:8010）からホスト名だけを取り出す。"""
    try:
        return (urlsplit(f"//{host_header}").hostname or "").lower()
    except ValueError:
        return ""


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.middleware("http")
async def _reject_foreign_hosts(request: Request, call_next):
    """DNS リバインディング対策: Host がローカル・許可ホスト以外のリクエストを拒否する。

    攻撃サイトのドメインを 127.0.0.1 に向け直すと、ブラウザはそのサイトと同一オリジンとして
    この API を読めてしまう（GET は Origin を送らないため Origin の確認では防げない）。
    そのとき Host は攻撃サイトのドメインになるので、ここで止める。
    """
    if _host_name(request.headers.get("host", "")) not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "許可されていないホストです"}, status_code=403,
                            headers=_SECURITY_HEADERS)
    return await call_next(request)


@app.middleware("http")
async def _reject_foreign_origins(request: Request, call_next):
    """localhost CSRF 対策: Origin がローカル以外のブラウザ発リクエストを拒否する。

    この API は認証を持たないため、multipart などプリフライト不要の POST は
    任意サイトから送信できてしまう（アプリ終了・モデル登録など）。
    Origin 未送信（curl・ファイル配信でない同一オリジン環境）は通し、
    送信される場合はループバック系ホストか許可オリジンのみ通す。
    """
    origin = request.headers.get("origin")
    if origin:
        allowed = origin.rstrip("/").lower() in _CONFIGURED_ORIGINS
        if not allowed:
            try:
                host = (urlsplit(origin).hostname or "").lower()
            except ValueError:
                host = ""
            allowed = host in _LOOPBACK_HOSTS
        if not allowed:
            return JSONResponse({"detail": "許可されていないオリジンです"}, status_code=403,
                                headers=_SECURITY_HEADERS)
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


def _is_plain_relative(path: str) -> bool:
    """dist 配下を指す素直な相対パスか。

    Windows では UNC パス（`//host/share`。バックスラッシュ区切りも同じ）を resolve するだけで SMB 接続が起き、
    資格情報が外へ漏れうる。ファイルシステムに触れる前に、区切り・ドライブ・".." を弾く。
    """
    if "\\" in path or ":" in path or path.startswith("/"):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # dist 直下の静的ファイル（favicon・ロゴ等）は実体を返し、
        # それ以外は SPA として index.html にフォールバックする。
        if full_path and _is_plain_relative(full_path):
            dist_root = _DIST.resolve()
            candidate = (dist_root / full_path).resolve()
            if dist_root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
