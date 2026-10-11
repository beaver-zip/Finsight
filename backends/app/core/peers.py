"""피어: 같은 업종(표준산업분류 중분류 2자리) 상장사 중 시가총액 상위. 후보 목록은 data/universe.json
(scripts/build_universe.py, add_universe_mcap.py로 만든다), 지표는 리포트 시점에 DART·시세로 다시 계산한다.

PER·PBR은 보통주 주가 ÷ 주당 값(지배주주 순이익·자본 ÷ 보통+우선 유통주식수), 시가총액은 보통+우선 합산.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from app.core.dart import financials as dart_fin
from app.core.dart.client import DartClient
from app.core.dart.history import RC, cum_value, value
from app.core.dart.shares import share_counts
from app.core.market import daily_close, pref_candidates, ticker_for
from app.domain.metrics import industry_group
from app.domain.models import CorpProfile
from app.settings import BACKEND_DIR

log = logging.getLogger(__name__)
UNIVERSE = BACKEND_DIR / "data" / "universe.json"


@lru_cache
def load_universe() -> list[dict]:
    try:
        return json.loads(UNIVERSE.read_text())["rows"]
    except Exception as e:  # noqa: BLE001
        log.warning("universe.json 없음: %s", e)
        return []


def peer_candidates(corp: CorpProfile, n: int = 6) -> list[dict]:
    code = (corp.induty_code or "").strip()
    if not code:
        return []
    u = [r for r in load_universe() if r.get("stock_code") != corp.stock_code and r.get("mcap_approx")]
    for plen in (2, 1):
        same = [r for r in u if (r.get("induty_code") or "").startswith(code[:plen])]
        if len(same) >= 3:
            return sorted(same, key=lambda r: -r["mcap_approx"])[:n]
    return []


@dataclass
class PeerRow:
    corp_code: str
    stock_code: str
    name: str
    is_self: bool
    mcap: float | None              # 원, 보통+우선
    ni_ttm: float | None            # 원, 지배주주
    equity: float | None            # 원, 기말 지배주주 자본
    equity_avg: float | None
    price: float | None
    shares_total: int | None
    roe: float | None = None
    pbr: float | None = None
    per: float | None = None
    induty_code: str | None = None
    ni_q: float | None = None           # 최근 분기(3개월) 지배주주 순이익. 사업보고서 기준이면 None
    per_rr: float | None = None         # 최근 분기 순이익 × 4 기준 PER
    group: str = "general"              # 금융 세트 판정(industry_group). 대상 회사와 다르면 피어에서 뺀다


def _f(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


async def last_close(ticker: str) -> float | None:
    from datetime import date, timedelta

    px = await daily_close(ticker, date.today() - timedelta(days=14))
    return px[-1][1] if px else None


async def pref_price(stock_code: str, corp_cls: str | None) -> float | None:
    for c in pref_candidates(stock_code):
        p = await last_close(ticker_for(c, corp_cls))
        if p:
            return p
    return None


async def metrics_for(dart: DartClient, corp_code: str, stock_code: str, corp_cls: str | None, name: str,
                      year: int, q: int, *, is_self: bool = False, induty_code: str | None = None) -> PeerRow | None:
    """(year, q) 보고서 기준 지표. 4분기면 사업연도 값, 아니면 최근 4개 분기(전년 연간 + 당해 누적 − 전년 동기 누적)."""
    try:
        cur = await dart_fin.get_snapshot(dart, corp_code, year, RC[q])
        if cur is None:
            return None
        fy_prev = await dart_fin.get_snapshot(dart, corp_code, year - 1, "11011")
        same_prev = None if q == 4 else await dart_fin.get_snapshot(dart, corp_code, year - 1, RC[q])
        sh = await share_counts(dart, corp_code, year, RC[q]) or await share_counts(dart, corp_code, year - 1, "11011")
    except Exception as e:  # noqa: BLE001
        log.info("피어 지표 실패 %s: %s", name, e)
        return None
    ni_cur = _f(cum_value(cur, "net_income_owners")) or _f(cum_value(cur, "net_income"))
    if q == 4:
        ni_ttm = ni_cur
    else:
        a = _f(cum_value(fy_prev, "net_income_owners")) or _f(cum_value(fy_prev, "net_income"))
        b = _f(cum_value(same_prev, "net_income_owners")) or _f(cum_value(same_prev, "net_income"))
        ni_ttm = None if None in (ni_cur, a, b) else ni_cur + a - b
    eq = _f(value(cur, "equity_owners")) or _f(value(cur, "total_equity"))
    eq_prev_src = fy_prev if q == 4 else same_prev
    eq0 = (_f(value(eq_prev_src, "equity_owners")) or _f(value(eq_prev_src, "total_equity"))) if eq_prev_src else None
    eq_avg = (eq + eq0) / 2 if eq is not None and eq0 is not None else eq
    price = await last_close(ticker_for(stock_code, corp_cls))
    pref_px = await pref_price(stock_code, corp_cls) if sh and sh.pref_out else None
    total = sh.total_out if sh else None
    mcap = None
    if price and sh:
        mcap = price * sh.common_out + (pref_px * sh.pref_out if pref_px else 0)
    row = PeerRow(corp_code, stock_code, name, is_self, mcap, ni_ttm, eq, eq_avg, price, total, induty_code=induty_code,
                  group=industry_group(cur, induty_code))
    if q < 4:
        row.ni_q = _f(value(cur, "net_income_owners", "thstrm_amount")) or _f(value(cur, "net_income", "thstrm_amount"))
    if ni_ttm is not None and eq_avg:
        row.roe = ni_ttm / eq_avg * 100
    if price and total and eq:
        row.pbr = price / (eq / total)
    if price and total and ni_ttm and ni_ttm > 0:
        row.per = price / (ni_ttm / total)
    if price and total and row.ni_q and row.ni_q > 0:
        row.per_rr = price / (row.ni_q * 4 / total)
    return row


async def peer_rows(dart: DartClient, corp: CorpProfile, year: int, q: int, n: int = 6) -> list[PeerRow]:
    """같은 중분류에서 시가총액 순으로 n개. 지주회사(64992)처럼 한 코드에 금융지주와 일반 지주가 섞이므로
    대상 회사와 금융 세트 판정이 다른 후보는 빼고 다음 후보로 채운다(후보는 최대 3n개까지 본다)."""
    me = await metrics_for(dart, corp.corp_code, corp.stock_code or "", corp.corp_cls, corp.corp_name, year, q,
                           is_self=True, induty_code=corp.induty_code)
    group = me.group if me else "general"
    cands = peer_candidates(corp, n * 3)
    rows = [me] if me else []
    picked = 0
    for i in range(0, len(cands), n):
        if picked >= n:
            break
        batch = cands[i:i + n]
        got = await asyncio.gather(*(metrics_for(dart, c["corp_code"], c["stock_code"], c["corp_cls"], c["name"], year, q,
                                                 induty_code=c.get("induty_code")) for c in batch))
        for r in got:
            if r is not None and r.group == group and picked < n:
                rows.append(r)
                picked += 1
    return sorted(rows, key=lambda r: -(r.mcap or 0))
