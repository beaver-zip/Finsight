"""fnlttSinglAcntAll → FinancialSnapshot. CFS가 013이면 OFS로 다시 부른다."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.core.cache import get_cache
from app.core.dart.client import DartClient, DartNoData
from app.domain.models import FinancialLine, FinancialSnapshot

_PERIOD = {"11013": ("1분기 누적", "1–3월"), "11012": ("반기 누적", "1–6월"),
           "11014": ("3분기 누적", "1–9월"), "11011": ("사업연도", "1–12월")}
_SJ = {"BS", "IS", "CIS", "CF", "SCE"}
_CACHE_NS = "dart.fin.v2"          # 기존 무기한 캐시를 재사용하지 않는다
_CACHE_TTL = 3_600
_EMPTY_TTL = 300


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
    """정상 응답은 1시간, 빈 응답은 5분 캐시. 키에 기대 접수번호(목록의 최종 정정본)를 넣어 새 정정이 나오면
    한 번 다시 받는다. 재무 API가 계속 옛 번호를 주면(첨부정정, 반영 지연) 그 값을 TTL 동안 그대로 쓴다."""
    cache = get_cache()
    for fs_div in ("CFS", "OFS"):
        key = f"{corp_code}:{bsns_year}:{reprt_code}:{fs_div}:{expected_rcept_no or ''}"
        rows = await cache.get(_CACHE_NS, key)
        if rows is None:
            try:
                rows = await _fetch(dart, corp_code, bsns_year, reprt_code, fs_div)
            except DartNoData:
                rows = []
            await cache.set(_CACHE_NS, key, rows, ttl_s=_CACHE_TTL if rows else _EMPTY_TTL)
        if rows:
            return FinancialSnapshot(corp_code=corp_code, rcept_no=rows[0]["rcept_no"], bsns_year=bsns_year,
                                     reprt_code=reprt_code, fs_div=fs_div,  # type: ignore[arg-type]
                                     period_label=period_label(bsns_year, reprt_code, acc_mt),
                                     lines=parse_lines(rows))
    return None
