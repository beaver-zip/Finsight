"""주식 총수(stockTotqySttus)와 배당 사항(alotMatter). 정기보고서 단위라 만료 없이 캐시한다."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.cache import get_cache
from app.core.dart.client import DartClient, DartNoData, cached

log = logging.getLogger(__name__)
MAX_SHARES = 7_000_000_000   # 국내 최다인 삼성전자 보통주 발행주식이 약 59억 주


def _int(s) -> int:
    t = str(s or "").replace(",", "").strip()
    return int(t) if t.lstrip("-").isdigit() else 0


@dataclass
class Shares:
    year: int
    reprt_code: str
    common_issued: int
    pref_issued: int
    common_treasury: int
    pref_treasury: int
    common_out: int
    pref_out: int
    rcept_no: str | None = None

    @property
    def total_out(self) -> int:
        return self.common_out + self.pref_out

async def _json(dart: DartClient, endpoint: str, corp_code: str, year: int, rc: str) -> list[dict] | None:
    cache = get_cache()
    key = f"{endpoint}:{corp_code}:{year}:{rc}"
    hit = await cache.get("dart.sh", key)
    if hit is not None:
        return hit or None
    try:
        rows = (await dart.json(endpoint, corp_code=corp_code, bsns_year=str(year), reprt_code=rc)).get("list", [])
    except DartNoData:
        rows = []
    await cache.set("dart.sh", key, rows)
    return rows or None


async def share_counts(dart: DartClient, corp_code: str, year: int, rc: str) -> Shares | None:
    rows = await _json(dart, "stockTotqySttus", corp_code, year, rc)
    if not rows:
        return None
    ci = pi = ct = pt = co = po = 0
    seen_c = False
    for r in rows:
        se = (r.get("se") or "").replace("\n", "").replace(" ", "")
        if "합계" in se or "비고" in se:
            continue
        issued, tre, out = _int(r.get("istc_totqy")), _int(r.get("tesstk_co")), _int(r.get("distb_stock_co"))
        if not out and issued:
            out = issued - tre
        if "우선" in se or "의결권없는" in se or "종류" in se:
            pi, pt, po = pi + issued, pt + tre, po + out
        elif "보통" in se or "의결권있는" in se:
            ci, ct, co, seen_c = ci + issued, ct + tre, co + out, True
    if not seen_c or ci <= 0:
        return None
    if co > MAX_SHARES or po > MAX_SHARES:
        # 일부 공시는 주식 수 칸에 금액이나 천 단위 오기가 있다(예: 4조 주). 쓰지 않는다.
        log.warning("주식 수 이상치 %s %s:%s 보통 %s 우선 %s", corp_code, year, rc, co, po)
        return None
    return Shares(year, rc, ci, pi, ct, pt, co, po, rows[0].get("rcept_no"))


@dataclass
class Dividend:
    year: int
    eps: int | None            # (연결)주당순이익
    dps_common: int | None     # 보통주 주당 현금배당금
    payout: float | None       # (연결)현금배당성향(%)
    rcept_no: str | None = None


async def dividend(dart: DartClient, corp_code: str, year: int) -> Dividend | None:
    rows = await _json(dart, "alotMatter", corp_code, year, "11011")
    if not rows:
        return None
    eps = dps = None
    payout = None
    for r in rows:
        se = (r.get("se") or "").replace(" ", "")
        kind = (r.get("stock_knd") or "").strip()
        val = str(r.get("thstrm") or "").replace(",", "").strip()
        if "주당순이익" in se and ("연결" in se or eps is None):
            eps = int(float(val)) if val.lstrip("-").replace(".", "").isdigit() else eps
        elif "주당현금배당금" in se and kind in ("보통주", "", "-"):
            dps = int(float(val)) if val.replace(".", "").isdigit() else dps
        elif "현금배당성향" in se and ("연결" in se or payout is None):
            try:
                payout = float(val)
            except ValueError:
                pass
    return Dividend(year, eps, dps, payout, rows[0].get("rcept_no"))


# ---------- 자기주식 취득 결정(주요사항보고서) ----------
@dataclass
class BuybackDecision:
    rcept_no: str
    decided: str | None          # 결정일(YYYY-MM-DD)
    shares_common: int
    amount_common: int           # 원
    start: str | None
    end: str | None
    purpose: str | None          # 취득 목적(소각, 임직원 주식보상, 주가 안정 등)
    method: str | None


def _kdate(s: str | None) -> str | None:
    import re

    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


async def buyback_decisions(dart: DartClient, corp_code: str, bgn: str, end: str) -> list[BuybackDecision]:
    """bgn·end: YYYYMMDD. 1시간 캐시."""
    async def fetch() -> list[dict]:
        try:
            return (await dart.json("tsstkAqDecsn", corp_code=corp_code, bgn_de=bgn, end_de=end)).get("list", [])
        except DartNoData:
            return []
    rows = await cached("dart.sh", f"tsstkAqDecsn:{corp_code}:{bgn}:{end}", 3600, fetch)
    out = [BuybackDecision(r["rcept_no"], _kdate(r.get("aq_dd")), _int(r.get("aqpln_stk_ostk")),
                           _int(r.get("aqpln_prc_ostk")), _kdate(r.get("aqexpd_bgd")), _kdate(r.get("aqexpd_edd")),
                           (r.get("aq_pp") or "").strip() or None, (r.get("aq_mth") or "").strip() or None)
           for r in rows]
    return sorted(out, key=lambda b: b.rcept_no)
