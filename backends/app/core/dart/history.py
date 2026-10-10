"""분기 이력(2016년~)과 연간·누적 항목. 모두 fnlttSinglAcntAll 스냅샷(만료 없는 캐시)에서 계산한다.

분기 손익은 1~3분기는 보고서의 3개월 값, 4분기는 사업보고서 연간 값에서 3분기 누적을 뺀 값이다.
재무상태 항목(지배주주 자본 등)은 각 분기 말 값이다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.dart import financials as dart_fin
from app.core.dart.client import DartClient
from app.domain.accounts import STANDARD, find_line
from app.domain.models import FinancialSnapshot

log = logging.getLogger(__name__)
RC = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
RC_Q = {v: k for k, v in RC.items()}
FIRST_YEAR = 2016                  # OpenDART 분기 재무제표가 있는 첫해
PL_ITEMS = ("revenue", "operating_income", "net_income", "net_income_owners")
CASH_ITEMS = ("revenue", "net_income", "cfo", "capex", "intangible_capex", "buyback", "dividends_paid",
              "cash", "st_fin", "st_amort", "st_fvpl", "st_borrow", "cur_ltd", "bonds", "lt_borrow",
              "receivables", "inventory", "equity_owners")


def qkey(year: int, q: int) -> str:
    return f"{q}Q{str(year)[2:]}"


def quarters_between(first: tuple[int, int], last: tuple[int, int]) -> list[tuple[int, int]]:
    (y, q), out = first, []
    while (y, q) <= last:
        out.append((y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def prev_quarter(year: int, q: int, n: int = 1) -> tuple[int, int]:
    for _ in range(n):
        year, q = (year, q - 1) if q > 1 else (year - 1, 4)
    return year, q


def value(snap: FinancialSnapshot | None, metric: str, column: str = "thstrm_amount") -> Decimal | None:
    if snap is None:
        return None
    f = find_line(snap, metric)
    return None if f is None else getattr(f.line, column)


def cum_value(snap: FinancialSnapshot | None, metric: str) -> Decimal | None:
    """누적 값. 손익은 분·반기면 thstrm_add_amount, 사업보고서면 thstrm_amount. 현금흐름·재무상태는 thstrm_amount."""
    if snap is None:
        return None
    pl = STANDARD[metric].tables[0] in ("IS", "CIS")
    col = "thstrm_add_amount" if (pl and snap.reprt_code != "11011") else "thstrm_amount"
    v = value(snap, metric, col)
    if v is None and pl and col == "thstrm_add_amount":      # 1분기 보고서는 누적 칸이 비어 있기도 하다
        v = value(snap, metric, "thstrm_amount") if snap.reprt_code == "11013" else None
    return v


@dataclass
class QRow:
    key: str
    year: int
    q: int
    revenue: Decimal | None = None
    operating_income: Decimal | None = None
    net_income: Decimal | None = None
    net_income_owners: Decimal | None = None
    equity_owners: Decimal | None = None
    rcept_no: str | None = None
    fs_div: str | None = None
    preliminary: bool = False

    def get(self, item: str) -> Decimal | None:
        return getattr(self, item)


@dataclass
class History:
    quarters: list[QRow]
    snapshots: dict[tuple[int, int], FinancialSnapshot] = field(default_factory=dict)

    def by_key(self) -> dict[str, QRow]:
        return {r.key: r for r in self.quarters}


async def _snap(dart: DartClient, corp_code: str, y: int, q: int, acc_mt: int | None) -> FinancialSnapshot | None:
    try:
        return await dart_fin.get_snapshot(dart, corp_code, y, RC[q], acc_mt=acc_mt)
    except Exception as e:  # noqa: BLE001
        log.info("스냅샷 실패 %s %s %s: %s", corp_code, y, RC[q], e)
        return None


async def quarterly_history(dart: DartClient, corp_code: str, last: tuple[int, int], *,
                            first_year: int = FIRST_YEAR, acc_mt: int | None = 12) -> History:
    """first_year 1분기부터 last 분기까지. 보고서가 없는 분기는 값이 비어 있다."""
    periods = quarters_between((first_year, 1), last)
    snaps_list = await asyncio.gather(*(_snap(dart, corp_code, y, q, acc_mt) for y, q in periods))
    snaps = {p: s for p, s in zip(periods, snaps_list) if s is not None}
    rows = []
    for (y, q) in periods:
        s = snaps.get((y, q))
        r = QRow(key=qkey(y, q), year=y, q=q, rcept_no=s.rcept_no if s else None, fs_div=s.fs_div if s else None)
        if s is not None:
            for it in PL_ITEMS:
                if q < 4:
                    v = value(s, it, "thstrm_amount")
                else:
                    fy, s3 = value(s, it, "thstrm_amount"), cum_value(snaps.get((y, 3)), it)
                    v = None if fy is None or s3 is None else fy - s3
                setattr(r, it, v)
            r.equity_owners = value(s, "equity_owners") or value(s, "total_equity")
        rows.append(r)
    # 앞쪽의 빈 분기(상장 전 등)는 잘라 낸다
    while rows and rows[0].revenue is None and rows[0].equity_owners is None:
        rows.pop(0)
    return History(quarters=rows, snapshots=snaps)


async def cumulative_items(dart: DartClient, corp_code: str, periods: list[tuple[int, int]], *,
                           acc_mt: int | None = 12, snapshots: dict | None = None) -> dict[tuple[int, int], dict]:
    """(연도, 분기) → 누적 손익·현금흐름·기말 재무상태 항목. 분기 4는 사업연도 전체."""
    out = {}
    for y, q in periods:
        s = (snapshots or {}).get((y, q)) or await _snap(dart, corp_code, y, q, acc_mt)
        if s is None:
            continue
        row = {it: cum_value(s, it) for it in CASH_ITEMS}
        row["rcept_no"] = s.rcept_no
        out[(y, q)] = row
    return out
