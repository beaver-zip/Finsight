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
from app.core.dart import original
from app.core.dart.client import DartClient
from app.domain.accounts import STANDARD, find_line
from app.domain.models import FinancialSnapshot

log = logging.getLogger(__name__)
RC = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
RC_Q = {v: k for k, v in RC.items()}
FIRST_YEAR = 2016                  # OpenDART 분기 재무제표가 있는 첫해
ORIGINAL_QUARTERS = 20             # 재무 API에 없는 분기를 원문에서 읽는 범위(최근 5년)
PL_ITEMS = ("revenue", "operating_income", "net_income", "net_income_owners")
CASH_ITEMS = ("revenue", "operating_income", "net_income", "net_income_owners", "cfo", "capex", "intangible_capex", "buyback", "dividends_paid",
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


async def from_original(dart: DartClient, corp_code: str, periods: list[tuple[int, int]],
                        acc_mt: int | None) -> dict[tuple[int, int], FinancialSnapshot]:
    """재무 API에 없는 분기를 정기보고서 원문 재무제표에서 읽는다(원문이 없는 분기는 건너뛴다)."""
    if not periods:
        return {}
    try:
        reports = await original.periodic_reports(dart, corp_code, min(y for y, _ in periods), acc_mt)
    except Exception as e:  # noqa: BLE001
        log.info("정기보고서 목록 실패 %s: %s", corp_code, e)
        return {}
    out = {}
    for y, q in periods:
        rc = reports.get((y, RC[q]))
        if rc is None:
            continue
        try:
            rows, fs_div = await original.statements(dart, rc, RC[q])
        except Exception as e:  # noqa: BLE001
            log.info("원문 재무제표 실패 %s %s: %s", corp_code, rc, e)
            continue
        if rows:
            out[(y, q)] = FinancialSnapshot(corp_code=corp_code, rcept_no=rc, bsns_year=y, reprt_code=RC[q],  # type: ignore[arg-type]
                                            fs_div=fs_div, period_label=dart_fin.period_label(y, RC[q], acc_mt),  # type: ignore[arg-type]
                                            lines=dart_fin.parse_lines(rows))
    return out


async def quarterly_history(dart: DartClient, corp_code: str, last: tuple[int, int], *,
                            first_year: int = FIRST_YEAR, acc_mt: int | None = 12, use_original: bool = True) -> History:
    """first_year 1분기부터 last 분기까지. 보고서가 없는 분기는 값이 비어 있다.

    최근 ORIGINAL_QUARTERS 분기 중 재무 API가 비어 있는 분기는 원문 재무제표로 채운다(use_original).
    """
    periods = quarters_between((first_year, 1), last)
    snaps_list = await asyncio.gather(*(_snap(dart, corp_code, y, q, acc_mt) for y, q in periods))
    snaps = {p: s for p, s in zip(periods, snaps_list) if s is not None}
    if use_original:
        recent = set(periods[-ORIGINAL_QUARTERS:])
        snaps.update(await from_original(dart, corp_code, [p for p in periods if p in recent and p not in snaps], acc_mt))
    rows = []
    for (y, q) in periods:
        s = snaps.get((y, q))
        r = QRow(key=qkey(y, q), year=y, q=q, rcept_no=s.rcept_no if s else None, fs_div=s.fs_div if s else None)
        if s is not None:
            for it in PL_ITEMS:
                if q < 4:
                    v = value(s, it, "thstrm_amount")
                    if v is None and q > 1:     # 3개월 칸이 없으면 누적에서 직전 분기 누적을 뺀다
                        c, p = cum_value(s, it), cum_value(snaps.get((y, q - 1)), it)
                        v = None if c is None or p is None else c - p
                else:
                    fy, s3 = value(s, it, "thstrm_amount"), cum_value(snaps.get((y, 3)), it)
                    v = None if fy is None or s3 is None else fy - s3
                setattr(r, it, v)
            if s.fs_div == "OFS" and r.net_income_owners is None:   # 별도 재무제표에는 지배주주 귀속 구분이 없다
                r.net_income_owners = r.net_income
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
        row["fs_div"] = s.fs_div
        out[(y, q)] = row
    return out
