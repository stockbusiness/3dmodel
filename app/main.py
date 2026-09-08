"""入口。仕様第3章・第12章。"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes import assets, auth_routes, experiments, files, generations, reports
from app.templating import TEMPLATES_DIR  # noqa: F401  （テンプレート探索の初期化）

# CSPの外部接続は限定する。model-viewer は同一オリジンの固定版のみ（仕様第12章）。
#
# 緩めている2点と理由（いずれも外部への接続は許していない）：
# - script-src の 'wasm-unsafe-eval'：model-viewer が WebAssembly を初期化するため。
#   これが無いと3D表示が動かない。任意スクリプトの実行を許すものではない。
# - style-src の 'unsafe-inline'：model-viewer が Shadow DOM に style 属性を当てるため。
#   スクリプトの inline は許していない。
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'self'"
)


def create_app() -> FastAPI:
    settings = get_settings()
    settings.ensure_dirs()

    app = FastAPI(title="3Dアート品質検証システム", docs_url=None, redoc_url=None, openapi_url=None)

    static_dir = TEMPLATES_DIR.parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(auth_routes.router)
    app.include_router(experiments.router)
    app.include_router(assets.router)
    app.include_router(generations.router)
    app.include_router(reports.router)
    app.include_router(files.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # 画面は未認証ならログイン画面へ送る。API は 401 のまま返す（仕様第7章）
        is_api = request.url.path.startswith("/api/")
        if exc.status_code == status.HTTP_401_UNAUTHORIZED and not is_api:
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
