"""fnlttSinglAcntAll → FinancialSnapshot. CFS가 013이면 OFS로 다시 부른다."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.core.cache import get_cache
from app.core.dart.client import DartClient, DartNoData
from app.domain.models import FinancialLine, FinancialSnapshot

_PERIOD = {"11013": ("1분기 누적", "1–3월"), "11012": ("반기 누적", "1–6월"),
           "11014": ("3분기 누적", "1–9월"), "11011": ("사업연도", "1–12월")}
_SJ = {"BS", "IS", "CIS", "CF", "SCE"}


def to_amount(s: str | None) -> Decimal | None:
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t in {"", "-"}:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def period_label(bsns_year: int, reprt_code: str, acc_mt: int | None = 12) -> str:
    name, months = _PERIOD[reprt_code]
    return f"{bsns_year}년 {name}" + (f"({months})" if (acc_mt or 12) == 12 else "")


def parse_lines(rows: list[dict]) -> list[FinancialLine]:
    lines = []
    for r in rows:
        sj = r.get("sj_div")
        if sj not in _SJ:
            continue
        try:
            ord_ = int(r.get("ord") or 0)
        except ValueError:
            ord_ = 0
        lines.append(FinancialLine(
            sj_div=sj, account_id=(r.get("account_id") or "").strip(), account_nm=(r.get("account_nm") or "").strip(),
            ord=ord_, thstrm_amount=to_amount(r.get("thstrm_amount")),
            thstrm_add_amount=to_amount(r.get("thstrm_add_amount")),
            frmtrm_amount=to_amount(r.get("frmtrm_amount")), frmtrm_q_amount=to_amount(r.get("frmtrm_q_amount")),
            frmtrm_add_amount=to_amount(r.get("frmtrm_add_amount")), currency=r.get("currency") or "KRW"))
    return lines


async def _fetch(dart: DartClient, corp_code: str, bsns_year: int, reprt_code: str, fs_div: str) -> list[dict]:
    data = await dart.json("fnlttSinglAcntAll", corp_code=corp_code, bsns_year=str(bsns_year),
                           reprt_code=reprt_code, fs_div=fs_div)
    return data.get("list", [])


async def get_snapshot(dart: DartClient, corp_code: str, bsns_year: int, reprt_code: str, *,
                       acc_mt: int | None = 12, expected_rcept_no: str | None = None) -> FinancialSnapshot | None:
    """만료 없는 캐시. 캐시된 rcept_no가 최종본(expected_rcept_no)과 다르면(정정 공시) 다시 받는다."""
    cache = get_cache()
    for fs_div in ("CFS", "OFS"):
        key = f"{corp_code}:{bsns_year}:{reprt_code}:{fs_div}"
        rows = await cache.get("dart.fin", key)
        if rows is not None and rows and expected_rcept_no and rows[0].get("rcept_no") != expected_rcept_no:
            rows = None
        if rows is None:
            try:
                rows = await _fetch(dart, corp_code, bsns_year, reprt_code, fs_div)
            except DartNoData:
                rows = []
            await cache.set("dart.fin", key, rows)
        if rows:
            return FinancialSnapshot(corp_code=corp_code, rcept_no=rows[0]["rcept_no"], bsns_year=bsns_year,
                                     reprt_code=reprt_code, fs_div=fs_div,  # type: ignore[arg-type]
                                     period_label=period_label(bsns_year, reprt_code, acc_mt),
                                     lines=parse_lines(rows))
    return None
