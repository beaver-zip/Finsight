"""list.json → 최신 정기보고서, 공시 이벤트(주요사항보고 B·거래소공시 I)."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.core.cache import make_key
from app.core.dart.client import DartClient, DartNoData, cached
from app.domain.models import CorpProfile, Disclosure, PeriodicFiling

HOUR = 3_600
_REPORT = re.compile(r"(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


async def _list(dart: DartClient, *, corp_code: str, bgn: date, end: date, pblntf_ty: str,
                last_reprt_at: str = "Y") -> list[dict]:
    params = dict(corp_code=corp_code, bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                  pblntf_ty=pblntf_ty, last_reprt_at=last_reprt_at)
    async def fetch() -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            try:
                data = await dart.json("list", **params, page_no=page, page_count=100)
            except DartNoData:
                break
            rows += data.get("list", [])
            if page >= int(data.get("total_page", 1)):
                break
            page += 1
        return rows

    # 키에 날짜 대신 기간 길이를 넣는다: 날이 바뀌어도 같은 키라 점검 때 어제 목록을 쓸 수 있다.
    # shortcut: 과거 날짜로 끝나는 같은 길이 조회와 키를 함께 쓴다. 과거 시점 조회가 생기면 end를 키에 넣는다
    key = make_key(corp_code, pblntf_ty, last_reprt_at, (end - bgn).days)
    return await cached("dart.list", key, HOUR, fetch)


def parse_report_name(report_nm: str, acc_mt: int | None) -> tuple[int, str] | None:
    """보고서명과 보고 월로 (bsns_year, reprt_code)를 정한다.
    12월 결산이 아닌 회사의 bsns_year 규칙은 설계서 미확정 사양(확인 전까지 보고 월의 연도를 쓴다)."""
    m = _REPORT.search(report_nm)
    if not m:
        return None
    kind, year, month = m.group(1), int(m.group(2)), int(m.group(3))
    acc = acc_mt or 12
    if kind == "사업보고서":
        return year, "11011"
    if kind == "반기보고서":
        return year, "11012"
    diff = (month - acc) % 12
    if diff == 3:
        return year, "11013"
    if diff == 9:
        return year, "11014"
    return None


async def latest_periodic(dart: DartClient, corp: CorpProfile, *, today: date | None = None) -> PeriodicFiling | None:
    today = today or date.today()
    rows = await _list(dart, corp_code=corp.corp_code, bgn=today - timedelta(days=548), end=today, pblntf_ty="A")
    cands = []
    for r in rows:
        parsed = parse_report_name(r["report_nm"], corp.acc_mt)
        if parsed:
            cands.append((r["rcept_dt"], r, parsed))
    if not cands:
        return None
    _, r, (year, code) = max(cands, key=lambda x: (x[0], x[1]["rcept_no"]))
    return PeriodicFiling(rcept_no=r["rcept_no"], report_nm=r["report_nm"].strip(), rcept_dt=_d(r["rcept_dt"]),
                          bsns_year=year, reprt_code=code)  # type: ignore[arg-type]


async def disclosures(dart: DartClient, corp_code: str, *, days: int = 90,
                      today: date | None = None) -> list[Disclosure]:
    today = today or date.today()
    out: list[Disclosure] = []
    for ty in ("B", "I"):
        for r in await _list(dart, corp_code=corp_code, bgn=today - timedelta(days=days), end=today, pblntf_ty=ty):
            out.append(Disclosure(rcept_no=r["rcept_no"], report_nm=r["report_nm"].strip(),
                                  rcept_dt=_d(r["rcept_dt"]), pblntf_ty=ty))
    out.sort(key=lambda d: (d.rcept_dt, d.rcept_no), reverse=True)
    return out
