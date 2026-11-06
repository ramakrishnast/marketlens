"""FastAPI app: JSON API under /api, built React frontend served from /."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import REPO_DIR, get_settings
from .providers.base import ProviderError

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
log = logging.getLogger("marketlens")

FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST", REPO_DIR / "frontend" / "dist"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    log.info("MarketLens starting: DATA_MODE=%s, AI=%s, db=%s", s.data_mode,
             f"OpenAI {s.openai_model}" if s.ai_enabled else "off (no OPENAI_API_KEY)", db.describe()["target"])
    sched = None
    if s.scheduler_enabled and os.environ.get("MARKETLENS_TESTING") != "1":
        try:
            from .scheduler import start_scheduler
            sched = start_scheduler()
        except ImportError:
            pass
    yield
    if sched is not None:
        sched.shutdown(wait=False)
    db.reset_connection()


from .api.jsonresp import SafeJSONResponse  # noqa: E402

app = FastAPI(title="MarketLens", version="1.0", lifespan=lifespan, default_response_class=SafeJSONResponse)


@app.exception_handler(ProviderError)
async def provider_error(_: Request, exc: ProviderError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Data source unavailable: {exc}"})


def _include_routers() -> None:
    import importlib

    for mod in ["core", "research", "news", "backtest", "chat", "briefing", "macro", "congress"]:
        try:
            m = importlib.import_module(f".api.{mod}", __package__)
        except ModuleNotFoundError as e:
            if e.name and e.name.endswith(f"api.{mod}"):
                continue
            raise
        app.include_router(m.router)


_include_routers()


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
