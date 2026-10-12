"""/report/{corp_code}: HTML, /stream(SSE), /json(CompanyReport).

완성 리포트는 (corp_code, 최신 정기보고서 rcept_no, 그 뒤 최신 잠정실적 rcept_no)로 7일 캐시한다.
잠정실적이 새로 나오면 키가 바뀌어 다시 만든다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.core import budget
from app.core.cache import get_cache
from app.core.dart import corp as dart_corp
from app.core.dart import filings as dart_filings
from app.core.dart.client import DartError, DartLimit
from app.core.dart.prelim import prelim_filings
from app.deps import AppState, get_state
from app.domain.dates import KST
from app.domain.models import Citation, CompanyReport

log = logging.getLogger(__name__)
router = APIRouter()
WEEK = 7 * 86_400
VERSION = "v2.2"            # 사실표가 바뀌면 올린다(v2.2: 주석 부문 표, 원문 재무제표로 채운 분기)


def _to_report(out: dict, cost: float | None) -> CompanyReport:
    f = out.get("filing")
    cites = [Citation(kind="filing", id=f.rcept_no, label=f.report_nm,
                      url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={f.rcept_no}")] if f else []
    cites += [Citation(kind="news", id=n.url, label=n.title, url=n.url) for n in out.get("news", [])]
    data, col = out.get("data"), out.get("collected")
    fs = col.quarters[-1].fs_div if col is not None and col.quarters else None
    return CompanyReport(corp=out["corp"], filing=f, latest=data.latest.get("label") if data else None,
                         fs_div=fs if fs in ("CFS", "OFS") else None, metrics=out.get("metrics", {}),
                         data=data.to_json() if data else None, draft=out.get("draft"),
                         verification=out.get("verification"), disclosures=out.get("disclosures", []),
                         news=out.get("news", []), news_note=out.get("news_note"),
                         regulatory=out.get("regulatory", []), citations=cites, errors=out.get("errors", []),
                         cost_usd=cost)


async def _cache_key(st: AppState, corp_code: str):
    profile = await dart_corp.get_profile(st.dart, corp_code)
    filing = await dart_filings.latest_periodic(st.dart, profile)
    prelim = "-"
    if filing is not None:
        today = datetime.now(KST).date()
        try:
            fl = await prelim_filings(st.dart, corp_code, max(filing.rcept_dt, today - timedelta(days=120)), today)
            if fl:
                prelim = max(x.rcept_no for x in fl)
        except Exception as e:  # noqa: BLE001
            log.info("잠정실적 목록 실패(캐시 키): %s", e)
    return profile, f"{VERSION}:{corp_code}:{filing.rcept_no if filing else 'none'}:{prelim}"


def _config(st: AppState) -> dict:
    return {"configurable": {"dart": st.dart, "http": st.http, "legal_runner": st.legal_runner},
            "recursion_limit": 30, "metadata": {}}


async def build_report(st: AppState, corp_code: str) -> dict:
    cache = get_cache()
    profile, key = await _cache_key(st, corp_code)
    hit = await cache.get("report", key)
    if hit is not None:
        return hit
    usage = budget.start_request()
    cfg = _config(st)
    cfg["metadata"] = {"corp_code": corp_code}
    out = await st.company_graph.ainvoke({"corp": profile, "errors": []}, config=cfg)
    rep = _to_report(out, round(usage.cost_usd, 5))
    value = {"html": out.get("html", ""), "json": rep.model_dump(mode="json")}
    if out.get("draft") is not None and not usage.exceeded:   # 본문까지 만든 리포트만 캐시한다
        await cache.set("report", key, value, ttl_s=WEEK)
    return value


def _http_error(e: Exception) -> HTTPException:
    if isinstance(e, DartLimit):
        return HTTPException(429, "OpenDART 요청 한도를 넘었습니다")
    if isinstance(e, budget.BudgetExceeded):
        return HTTPException(402, str(e))
    if isinstance(e, budget.TokenBudgetExceeded):
        return HTTPException(429, str(e))
    if isinstance(e, DartError):
        return HTTPException(502, str(e))
    return HTTPException(500, f"보고서 생성 중 오류: {e}")


@router.get("/{corp_code}", response_class=PlainTextResponse, summary="기업 분석 보고서(HTML)")
async def report_html(corp_code: str, st: AppState = Depends(get_state)):
    try:
        return (await build_report(st, corp_code))["html"]
    except Exception as e:  # noqa: BLE001
        log.exception("report 실패")
        raise _http_error(e) from e


@router.get("/{corp_code}/json", response_model=CompanyReport, summary="구조화 리포트")
async def report_json(corp_code: str, st: AppState = Depends(get_state)):
    try:
        return (await build_report(st, corp_code))["json"]
    except Exception as e:  # noqa: BLE001
        raise _http_error(e) from e


def error_code(e: Exception) -> str:
    """SSE error 이벤트 code."""
    if isinstance(e, DartLimit):
        return "DART_LIMIT"
    if isinstance(e, budget.BudgetExceeded):
        return "BUDGET"
    if isinstance(e, budget.TokenBudgetExceeded):
        return "TOKEN_BUDGET"
    return "INTERNAL"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/{corp_code}/stream", summary="단계 진행 + 최종 HTML(SSE)")
async def report_stream(corp_code: str, st: AppState = Depends(get_state)):
    async def gen():
        try:
            profile, key = await _cache_key(st, corp_code)
            hit = await get_cache().get("report", key)
            if hit is not None:
                yield _sse("final", {"html": hit["html"], "cached": True})
                return
            usage = budget.start_request()
            final: dict = {}
            cfg = _config(st)
            async for mode, chunk in st.company_graph.astream({"corp": profile, "errors": []}, config=cfg,
                                                               stream_mode=["updates", "custom"]):
                if mode == "custom" and chunk.get("type") == "progress":
                    yield _sse("progress", {"stage": chunk["stage"], "detail": chunk["detail"]})
                elif mode == "updates":
                    for node, upd in chunk.items():
                        yield _sse("node", {"ns": ["company"], "node": node, "status": "end"})
                        if isinstance(upd, dict):
                            final.update({k: v for k, v in upd.items() if k != "errors"})
                            final.setdefault("errors", []).extend(upd.get("errors", []) or [])
            final["corp"] = profile
            rep = _to_report(final, round(usage.cost_usd, 5))
            if final.get("draft") is not None and not usage.exceeded:
                await get_cache().set("report", key, {"html": final.get("html", ""),
                                                      "json": rep.model_dump(mode="json")}, ttl_s=WEEK)
            for e in final.get("errors", []):
                yield _sse("error", {"code": "PARTIAL", "message": e, "partial": True})
            if (tb := budget.token_budget_event(usage)):
                yield _sse("error", tb)
            yield _sse("final", {"html": final.get("html", ""), "cost_usd": rep.cost_usd})
        except Exception as e:  # noqa: BLE001
            log.exception("report stream 실패")
            yield _sse("error", {"code": error_code(e), "message": str(e), "partial": False})

    return StreamingResponse(gen(), media_type="text/event-stream")
