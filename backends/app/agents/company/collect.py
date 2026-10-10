"""리포트 v2 원자료 수집: DART(분기 이력, 누적 항목, 주식 수, 배당, 자기주식, 잠정실적, 부문), 시세, 피어.

계산은 하지 않는다(analysis.py가 한다). 한 갈래가 실패하면 그 자료만 비워 두고 나머지는 그대로 모은다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.core.dart.client import DartClient
from app.core.dart.history import RC, RC_Q, History, QRow, cumulative_items, quarterly_history
from app.core.dart.prelim import Prelim, PrelimFiling, first_per_quarter, latest_prelim, prelim_filings
from app.core.dart.segments import segment_cumulative
from app.core.dart.shares import BuybackDecision, Dividend, Shares, buyback_decisions, dividend, share_counts
from app.core.market import daily_close, index_ticker, splits, ticker_for
from app.core.peers import PeerRow, peer_rows, pref_price
from app.domain.metrics import industry_group
from app.domain.models import CorpProfile, PeriodicFiling

log = logging.getLogger(__name__)
PX_START = date(2016, 1, 1)        # 분기 재무제표가 있는 첫해와 맞춘다(PBR 밴드)
EVENT_YEARS = 3                    # 잠정실적 발표 반응은 최근 3년+올해
SEGMENT_REPORTS = 9                # 부문 분기 값 8개를 만들려면 정기보고서 9개가 필요하다


@dataclass
class Collected:
    quarters: list[QRow] = field(default_factory=list)
    prelim: Prelim | None = None
    cum: dict[tuple[int, int], dict] = field(default_factory=dict)
    shares: dict[tuple[int, int], Shares] = field(default_factory=dict)
    splits: list[tuple[date, float]] = field(default_factory=list)
    dividends: dict[int, Dividend] = field(default_factory=dict)
    buybacks: list[BuybackDecision] = field(default_factory=list)
    px: list[tuple[date, float]] = field(default_factory=list)
    index_px: list[tuple[date, float]] = field(default_factory=list)
    index_name: str = "KOSPI"
    pref_px: float | None = None
    prelim_firsts: list[PrelimFiling] = field(default_factory=list)
    segments: dict[tuple[int, int], dict[str, dict[str, Decimal]]] = field(default_factory=dict)
    peers: list[PeerRow] = field(default_factory=list)
    industry: str = "general"
    errors: list[str] = field(default_factory=list)


async def _safe(name: str, coro, default, errors: list[str]):
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        log.warning("수집 %s 실패: %s", name, e)
        errors.append(f"collect.{name}: {type(e).__name__}: {e}")
        return default


def cash_periods(filing: PeriodicFiling) -> list[tuple[int, int]]:
    """현금흐름 표 열: 직전 사업연도까지 3개 연도 + (분·반기 보고서면) 전년 동기·당해 누적."""
    y, q = filing.bsns_year, RC_Q[filing.reprt_code]
    fy_last = y if q == 4 else y - 1
    out = [(fy_last - 2, 4), (fy_last - 1, 4), (fy_last, 4)]
    if q < 4:
        out += [(y - 1, q), (y, q)]
    return out


async def collect(dart: DartClient, http: httpx.AsyncClient, corp: CorpProfile, filing: PeriodicFiling,
                  today: date) -> Collected:
    out = Collected()
    err = out.errors
    y, q = filing.bsns_year, RC_Q[filing.reprt_code]
    acc = corp.acc_mt or 12
    code = corp.corp_code
    listed = bool(corp.stock_code)
    tk = ticker_for(corp.stock_code, corp.corp_cls) if listed else None
    itk, out.index_name = index_ticker(corp.corp_cls)

    async def none():
        return None

    async def empty():
        return []

    hist, px, ix, sp, prelim, pfl, bb, peers = await asyncio.gather(
        _safe("history", quarterly_history(dart, code, (y, q), acc_mt=acc), History(quarters=[]), err),
        _safe("price", daily_close(tk, PX_START) if tk else empty(), [], err),
        _safe("index", daily_close(itk, PX_START) if tk else empty(), [], err),
        _safe("splits", splits(tk) if tk else empty(), [], err),
        _safe("prelim", latest_prelim(dart, http, code, (y, q), today), None, err),
        _safe("prelim_list", prelim_filings(dart, code, date(today.year - EVENT_YEARS, 1, 1), today), [], err),
        _safe("buyback", buyback_decisions(dart, code, (today - timedelta(days=400)).strftime("%Y%m%d"),
                                           today.strftime("%Y%m%d")), [], err),
        _safe("peers", peer_rows(dart, corp, y, q) if listed else empty(), [], err),
    )
    out.quarters, out.px, out.index_px, out.splits, out.prelim, out.buybacks, out.peers = \
        hist.quarters, px, ix, sp, prelim, bb, peers
    out.prelim_firsts = first_per_quarter(pfl)
    if hist.snapshots:
        latest = hist.snapshots.get((y, q)) or hist.snapshots[max(hist.snapshots)]
        out.industry = industry_group(latest, corp.induty_code)

    periods = [(r.year, r.q) for r in hist.quarters]
    fy_last = y if q == 4 else y - 1
    seg_periods = [p for p in periods if p in hist.snapshots][-SEGMENT_REPORTS:]

    async def shares_all():
        got = await asyncio.gather(*(share_counts(dart, code, yy, RC[qq]) for yy, qq in periods),
                                   return_exceptions=True)
        return {p: s for p, s in zip(periods, got) if isinstance(s, Shares)}

    async def dividends_all():
        got = await asyncio.gather(*(dividend(dart, code, yy) for yy in range(fy_last - 2, fy_last + 1)),
                                   return_exceptions=True)
        return {d.year: d for d in got if isinstance(d, Dividend)}

    async def segments_all():
        got = await asyncio.gather(*(segment_cumulative(http, hist.snapshots[p].rcept_no) for p in seg_periods),
                                   return_exceptions=True)
        return {p: s for p, s in zip(seg_periods, got) if isinstance(s, dict) and s}

    out.shares, out.cum, out.dividends, out.segments = await asyncio.gather(
        _safe("shares", shares_all(), {}, err),
        _safe("cum", cumulative_items(dart, code, cash_periods(filing), acc_mt=acc, snapshots=hist.snapshots), {}, err),
        _safe("dividends", dividends_all(), {}, err),
        _safe("segments", segments_all(), {}, err),
    )
    last_sh = out.shares.get((y, q)) or (out.shares[max(out.shares)] if out.shares else None)
    if listed and last_sh and last_sh.pref_out:
        out.pref_px = await _safe("pref_price", pref_price(corp.stock_code, corp.corp_cls), None, err)
    return out
