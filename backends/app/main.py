"""Finsight v2 API. 공유 클라이언트와 컴파일된 그래프는 lifespan에서 한 번 만든다."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import aiosqlite
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.company.graph import build_company_graph
from app.agents.legal.graph import build_legal_graph, make_runner
from app.agents.legal.tools import LawTools
from app.agents.orchestrator.graph import build_orchestrator, serializer
from app.api import admin, chat, documents, financial, report
from app.core.cache import get_cache
from app.core.dart.client import DartClient
from app.core.search import tavily
from app.deps import AppState
from app.settings import BACKEND_DIR, get_settings
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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
    law_tools = await _open_law_tools()
    runner = make_runner(build_legal_graph(), law_tools) if law_tools else None
    db = get_settings().sqlite_path
    db = db if db.is_absolute() else BACKEND_DIR / db
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db))                  # 채팅 단기 메모리(thread_id별 대화)
    saver = AsyncSqliteSaver(conn, serde=serializer())
    app.state.fs = AppState(http=http, dart=DartClient(http), company_graph=build_company_graph(),
                            legal_runner=runner, law_tools=law_tools, chat_graph=build_orchestrator(saver))
    try:
        yield
    finally:
        await conn.close()
        if law_tools:
            await law_tools.close()
        await http.aclose()


async def _open_law_tools():
    """Weaviate·Neo4j가 꺼져 있으면 법령 리서치 없이 뜬다(리포트 규제 절은 건너뛴다)."""
    try:
        t = await LawTools.open()
        await t.graph.expand(["전자금융거래법:1"], hops=1, limit=1)
        return t
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("법령 리서치 비활성(Weaviate·Neo4j 연결 실패): %s", type(e).__name__)
        return None


app = FastAPI(title="Finsight v2 API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(report.router, prefix="/report", tags=["Report"])
app.include_router(financial.router, prefix="/financial", tags=["Financial"])
app.include_router(chat.router, prefix="/chat", tags=["Chat"])
app.include_router(documents.files_router, prefix="/files", tags=["Documents"])
app.include_router(documents.rag_router, prefix="/rag", tags=["Documents"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])


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
