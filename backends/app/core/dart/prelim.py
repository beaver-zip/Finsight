"""영업(잠정)실적(공정공시). 목록은 OpenDART list.json(last_reprt_at=N), 표는 공시 원문에서 읽는다.

- 이벤트 분석에는 분기마다 처음 낸 원 공시(정정 아님)의 날짜를 쓴다. last_reprt_at=Y로 받으면
  월초 조기 공시가 월말 [기재정정]에 가려져 빠진다.
- 증감률은 회사가 공시한 값을 그대로 쓴다. 금액이 0.01조원 단위로 반올림돼 있어 다시 계산하면 틀린다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from app.core.dart import viewer
from app.core.dart.client import DartClient
from app.core.dart.filings import _list

log = logging.getLogger(__name__)
UNITS = {"조원": Decimal(10) ** 12, "억원": Decimal(10) ** 8, "백만원": Decimal(10) ** 6, "천원": Decimal(10) ** 3,
         "원": Decimal(1)}
ROWS = {"revenue": ("매출액", "영업수익", "수익(매출액)", "매출"), "operating_income": ("영업이익",),
        "pretax_income": ("법인세비용차감전계속사업이익",), "net_income": ("당기순이익",),
        "net_income_owners": ("지배기업소유주지분순이익", "지배기업소유주지분당기순이익")}


@dataclass
class PrelimFiling:
    rcept_no: str
    rcept_dt: date
    report_nm: str
    corrected: bool

    @property
    def quarter_guess(self) -> tuple[int, int]:
        """12월 결산 기준: 1월 → 직전 연도 4분기, 4월 → 1분기, 7월 → 2분기, 10월 → 3분기."""
        y, m = self.rcept_dt.year, self.rcept_dt.month
        q = (m - 1) // 3
        return (y - 1, 4) if q == 0 else (y, q)


@dataclass
class PrelimRow:
    cur: Decimal | None = None          # 당기(원)
    prev: Decimal | None = None         # 직전 분기
    qoq: float | None = None            # 공시 증감률(%)
    yoy_base: Decimal | None = None     # 전년 동기
    yoy: float | None = None
    cum: Decimal | None = None          # 누계
    cum_prev: Decimal | None = None
    cum_yoy: float | None = None
    note: str | None = None             # 흑자전환 등


@dataclass
class Prelim:
    rcept_no: str
    rcept_dt: date
    report_nm: str
    year: int
    q: int
    unit: str
    consolidated: bool
    rows: dict[str, PrelimRow] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.q}Q{str(self.year)[2:]}"


def _num(tok: str) -> Decimal | None:
    t = tok.replace(",", "").replace("△", "-").strip()
    if t in ("", "-", "−"):
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _pct(tok: str) -> float | None:
    v = _num(tok)
    return None if v is None else float(v)


async def prelim_filings(dart: DartClient, corp_code: str, since: date, until: date | None = None) -> list[PrelimFiling]:
    until = until or date.today()
    rows = await _list(dart, corp_code=corp_code, bgn=since, end=until, pblntf_ty="I", last_reprt_at="N")
    out = []
    for r in rows:
        nm = r["report_nm"].strip()
        if "잠정" in nm and "실적" in nm:
            d = r["rcept_dt"]
            out.append(PrelimFiling(r["rcept_no"], date(int(d[:4]), int(d[4:6]), int(d[6:])), nm,
                                    nm.startswith("[")))
    out.sort(key=lambda f: (f.rcept_dt, f.rcept_no))
    return out


def first_per_quarter(filings: list[PrelimFiling]) -> list[PrelimFiling]:
    """분기마다 처음 낸 원 공시. 같은 분기에 연결·별도가 함께 나오면 먼저 접수된 것."""
    seen, out = set(), []
    for f in filings:
        if f.corrected:
            continue
        q = f.quarter_guess
        if q not in seen:
            seen.add(q)
            out.append(f)
    return out


def parse_prelim(cells: list[str], filing: PrelimFiling) -> Prelim | None:
    joined = " ".join(cells)
    mu = re.search(r"단위\s*:\s*(조원|억원|백만원|천원|원)", joined)
    unit = mu.group(1) if mu else "원"
    k = UNITS[unit]
    # 실적기간: '당기실적' 뒤 첫 날짜 두 개
    year = q = None
    for i, t in enumerate(cells):
        if t.replace(" ", "") == "당기실적":
            dates = [c for c in cells[i + 1:i + 6] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c)]
            if len(dates) >= 2:
                end = date.fromisoformat(dates[1])
                year, q = end.year, (end.month - 1) // 3 + 1
                break
    if year is None:
        year, q = filing.quarter_guess
    norm = [c.replace(" ", "") for c in cells]
    rows: dict[str, PrelimRow] = {}
    for metric, labels in ROWS.items():
        idx = next((i for i, c in enumerate(norm) if any(c == lb or c.startswith(lb + "(") for lb in labels)), None)
        if idx is None:
            continue
        r = PrelimRow()
        j = idx + 1
        if j < len(norm) and norm[j] == "당해실적":
            v = cells[j + 1:j + 8]
            if len(v) >= 6:
                r.cur, r.prev, r.qoq = (x * k if (x := _num(v[0])) is not None else None), \
                    (x * k if (x := _num(v[1])) is not None else None), _pct(v[2])
                r.yoy_base, r.yoy = (x * k if (x := _num(v[4])) is not None else None), _pct(v[5])
                flags = [t for t in (v[3], v[6] if len(v) > 6 else "") if t not in ("-", "")]
                r.note = flags[0] if flags else None
            j += 8
        if j < len(norm) and norm[j] == "누계실적":
            v = cells[j + 1:j + 8]
            if len(v) >= 6:
                r.cum = x * k if (x := _num(v[0])) is not None else None
                r.cum_prev = x * k if (x := _num(v[4])) is not None else None
                r.cum_yoy = _pct(v[5])
        if r.cur is not None:
            rows[metric] = r
    if "operating_income" not in rows and "revenue" not in rows:
        return None
    return Prelim(filing.rcept_no, filing.rcept_dt, filing.report_nm, year, q, unit,
                  consolidated="연결" in filing.report_nm, rows=rows)


async def fetch_prelim(http: httpx.AsyncClient, filing: PrelimFiling) -> Prelim | None:
    try:
        page = await viewer.document(http, filing.rcept_no)
    except Exception as e:  # noqa: BLE001
        log.info("잠정실적 원문 실패 %s: %s", filing.rcept_no, e)
        return None
    return parse_prelim(viewer.to_cells(page), filing) if page else None


async def latest_prelim(dart: DartClient, http: httpx.AsyncClient, corp_code: str, after: tuple[int, int],
                        today: date | None = None) -> Prelim | None:
    """정기보고서 마지막 분기(after)보다 뒤 분기의 잠정실적. 정정 공시가 있으면 가장 늦은 것(순이익까지 채워진 경우가 많다)."""
    today = today or date.today()
    fl = await prelim_filings(dart, corp_code, today - timedelta(days=120), today)
    # 가장 늦은 분기 → 연결 기준 → 늦게 접수된 것(정정본) 순
    for f in sorted(fl, key=lambda f: (f.quarter_guess, "연결" in f.report_nm, f.rcept_dt, f.rcept_no), reverse=True):
        if f.quarter_guess <= after:
            continue
        p = await fetch_prelim(http, f)
        if p is not None and (p.year, p.q) > after:
            return p
    return None
