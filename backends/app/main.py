"""Finsight v2 API. 공유 클라이언트와 컴파일된 그래프는 lifespan에서 한 번 만든다."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.company.graph import build_company_graph
from app.api import financial, report
from app.core.cache import get_cache
from app.core.dart.client import DartClient
from app.core.search import tavily
from app.deps import AppState
from app.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx는 INFO에서 요청 URL을 남긴다. OpenDART·법제처 URL에는 인증키가 들어 있으므로 올리지 않는다
for _name in ("httpx", "httpcore", "httpx2"):
    logging.getLogger(_name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    cache = get_cache()
    await cache.purge_expired()
    http = httpx.AsyncClient(timeout=10, follow_redirects=True)
    await tavily.init_free_credits(http)
    app.state.fs = AppState(http=http, dart=DartClient(http), company_graph=build_company_graph())
    try:
        yield
    finally:
        await http.aclose()


app = FastAPI(title="Finsight v2 API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(report.router, prefix="/report", tags=["Report"])
app.include_router(financial.router, prefix="/financial", tags=["Financial"])


@app.get("/", tags=["Root"])
async def root():
    return {"message": "Finsight v2 API"}


@app.get("/health/budget", tags=["Root"])
async def budget_status():
    c = get_cache()
    s = get_settings()
    return {"total_usd": round(await c.total_cost(), 4), "budget_usd": s.budget_usd,
            "by_service": await c.cost_by_service(), "tavily_credits": await c.total_credits("tavily"),
            "tavily_max_credits": s.tavily_max_credits}
