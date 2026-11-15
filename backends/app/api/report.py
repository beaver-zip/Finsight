"""/report/{corp_code}: HTML, /json(CompanyReport).

완성 리포트는 (corp_code, 최신 정기보고서 rcept_no, 그 뒤 최신 잠정실적 rcept_no)로 7일 캐시한다.
잠정실적이 새로 나오면 키가 바뀌어 다시 만든다.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

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
HOUR = 3_600                # 외부 수집이 일부 실패한 리포트: 장애가 풀리면 다시 만든다
VERSION = "v2.5"            # v2.5: 이전 리포트는 재무 수치가 틀리고 링크가 새 탭으로 열리지 않는다
# 장애로 빠졌을 수 있는 자료: collect 하위 갈래, guarded 노드 예외("news: ConnectTimeout: ...").
# 법령 서브그래프 미연결 같은 설정 문제는 예외 이름이 없어 걸리지 않는다
_TRANSIENT = re.compile(r"collect\.|(news|disclosures|regulatory): [A-Za-z_]\w*: ")


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
    today = datetime.now(KST).date()        # 그래프(get_filing)와 같은 KST 날짜
    filing = await dart_filings.latest_periodic(st.dart, profile, today=today)
    prelim = "-"
    if filing is not None:
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


def _cacheable(out: dict, usage: budget.RequestUsage) -> bool:
    ver = out.get("verification")
    return (out.get("draft") is not None and ver is not None and not usage.exceeded
            and all(i.type == "style" for i in ver.issues))   # 문체 지적만 남은 초안은 완성본이다


async def build_report(st: AppState, corp_code: str) -> dict:
    cache = get_cache()
    profile, key = await _cache_key(st, corp_code)
    hit = await cache.get("report", key)
    if hit is not None:
        return hit
    with budget.request_scope() as usage:
        cfg = _config(st)
        cfg["metadata"] = {"corp_code": corp_code}
        out = await st.company_graph.ainvoke({"corp": profile, "errors": []}, config=cfg)
        rep = _to_report(out, round(usage.cost_usd, 5))
        value = {"html": out.get("html", ""), "json": rep.model_dump(mode="json")}
        if _cacheable(out, usage):
            partial = any(_TRANSIENT.match(e) for e in out.get("errors", []))
            await cache.set("report", key, value, ttl_s=HOUR if partial else WEEK)
        return value


# 예외 → (HTTP 상태, SSE code). DartLimit은 DartError의 하위 클래스라 DartError(502)보다 먼저 본다.
_ERRORS = ((DartLimit, 429, "DART_LIMIT"), (budget.BudgetExceeded, 402, "BUDGET"),
           (budget.TokenBudgetExceeded, 429, "TOKEN_BUDGET"))


def _http_error(e: Exception) -> HTTPException:
    for cls, status, _ in _ERRORS:
        if isinstance(e, cls):
            return HTTPException(status, "OpenDART 요청 한도를 넘었습니다" if cls is DartLimit else str(e))
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
    return next((code for cls, _, code in _ERRORS if isinstance(e, cls)), "INTERNAL")
