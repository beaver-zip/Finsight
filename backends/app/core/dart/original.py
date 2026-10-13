"""정기보고서 원문(OpenDART document.xml)의 재무제표 표 → fnlttSinglAcntAll과 같은 모양의 행.

재무 API가 013(데이터 없음)을 주는 보고서가 있다. KB금융은 2023년 3분기 이전 보고서 전부가 그렇다.
그런 분기만 원문 'III. 재무에 관한 사항'의 '연결재무제표'(없으면 '재무제표') 표에서 읽는다.

- 계정 ID가 없어 계정명으로만 찾는다(account_id는 "-표준계정코드 미사용-").
- 금액은 표의 단위(백만원·천원)를 원으로 바꾼다.
- 열은 가장 큰 '제N기'(당기)만 쓴다. '3개월'은 thstrm_amount, '누적'은 thstrm_add_amount.
  당기 열이 하나뿐이면 1분기·사업보고서는 thstrm_amount, 반기·3분기는 누적(thstrm_add_amount)으로 본다.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date
from decimal import Decimal

import lxml.html

from app.core.cache import get_cache
from app.core.dart.client import DartClient
from app.core.dart.filings import _list, parse_report_name
from app.core.dart import viewer
from app.domain.accounts import normalize_name

log = logging.getLogger(__name__)
NO_ID = "-표준계정코드 미사용-"
VERSION = 2                        # 파서를 고치면 올린다(캐시 키에 들어가 다시 읽는다)
_UNIT = re.compile(r"단위\s*:\s*(백만원|천원|원)")
_UNITS = {"백만원": Decimal(10) ** 6, "천원": Decimal(1000), "원": Decimal(1)}
_TERM = re.compile(r"제\s*(\d+)\s*기")
_AMT = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")


def _amount(tok: str, k: Decimal) -> Decimal | None:
    t = tok.replace(" ", "")
    if not _AMT.match(t):
        return None
    neg = t.startswith("(") or t.startswith("-")
    v = Decimal(t.strip("()-").replace(",", ""))
    return (-v if neg else v) * k


def _section(xml: str) -> tuple[str, str] | None:
    """'N. 연결재무제표' 절 본문과 fs_div. 없으면 'N. 재무제표'(별도)."""
    titles = [(m.start(), m.end(), re.sub(r"\s+", "", m.group(1)))
              for m in re.finditer(r"<TITLE[^>]*>([^<]*)</TITLE>", xml)]
    for want, fs in ((r"^\d+\.연결재무제표$", "CFS"), (r"^\d+\.재무제표$", "OFS")):
        for i, (s, e, t) in enumerate(titles):
            if re.match(want, t):
                end = titles[i + 1][0] if i + 1 < len(titles) else len(xml)
                return xml[e:end], fs
    return None


def _kind(labels: list[str], heads: list[str]) -> str | None:
    if any("자본금" in h or "지배기업" in h for h in heads):     # 자본변동표
        return None
    keys = [normalize_name(x) for x in labels]
    if any(k.startswith("영업활동") for k in keys):
        return "CF"
    if any(k in ("자산총계", "부채총계", "자본총계", "부채와자본총계") for k in keys):
        return "BS"
    if any("순이익" in k or "순손실" in k for k in keys):
        return "PL"
    return None


def parse_statements(xml: str, reprt_code: str, rcept_no: str) -> tuple[list[dict], str]:
    """원문 XML → (행 목록, fs_div). 행은 fnlttSinglAcntAll 응답과 같은 키를 쓴다."""
    got = _section(xml)
    if not got:
        return [], "CFS"
    body, fs_div = got
    doc = lxml.html.fromstring(f"<div>{body}</div>")
    unit = Decimal(1)
    rows: list[dict] = []
    seen: set[str] = set()
    for t in doc.iter("table"):
        if (t.get("border") or "0") == "0":
            if (m := _UNIT.search(re.sub(r"\s+", " ", t.text_content()))):
                unit = _UNITS[m.group(1)]
            continue
        g = viewer.grid(t)
        if len(g) < 3:
            continue
        width = max(len(r) for r in g)
        g = [r + [""] * (width - len(r)) for r in g]
        nh = 0
        while nh < len(g) and not any(_amount(x, unit) is not None for x in g[nh][1:]):
            nh += 1
        if nh == 0 or nh >= len(g):
            continue
        heads = [" ".join(dict.fromkeys(g[i][j] for i in range(nh))) for j in range(width)]
        kind = _kind([r[0] for r in g[nh:]], heads[1:])
        if kind is None:
            continue
        sj = kind if kind != "PL" else ("IS" if "IS" not in seen else "CIS")
        if sj in seen:
            continue
        seen.add(sj)
        terms = {j: int(m.group(1)) for j in range(1, width) if (m := _TERM.search(heads[j]))}
        if not terms:
            continue
        cur = max(terms.values())
        cols = [j for j, n in terms.items() if n == cur]
        # 한 기간이 '세부·합계' 두 칸으로 나뉜 옛 형식도 있어, 같은 종류 칸 중 값이 있는 첫 칸을 쓴다
        three = [j for j in cols if "3개월" in heads[j]]
        cum = [j for j in cols if "누적" in heads[j]]
        if not three and not cum:
            if sj == "BS" or reprt_code in ("11013", "11011"):
                three = cols
            else:
                cum = cols
        pick = lambda r, js: next((v for j in js if (v := _amount(r[j], unit)) is not None), None)
        for i, r in enumerate(g[nh:]):
            label = re.sub(r"\s+", " ", r[0]).strip()
            if not label:
                continue
            v3, vc = pick(r, three), pick(r, cum)
            if sj != "BS" and reprt_code == "11013" and vc is None:
                vc = v3
            if v3 is None and vc is None:
                continue
            rows.append({"rcept_no": rcept_no, "sj_div": sj, "account_id": NO_ID, "account_nm": label,
                         "ord": str(i), "thstrm_amount": None if v3 is None else str(v3),
                         "thstrm_add_amount": None if vc is None else str(vc), "currency": "KRW"})
    return rows, fs_div


async def periodic_reports(dart: DartClient, corp_code: str, first_year: int, acc_mt: int | None) -> dict:
    """(연도, 보고서코드) → 최종 접수번호. 정정 공시가 있으면 마지막 정정본."""
    rows = await _list(dart, corp_code=corp_code, bgn=date(first_year, 1, 1), end=date.today(), pblntf_ty="A")
    out: dict[tuple[int, str], str] = {}
    for r in sorted(rows, key=lambda r: r["rcept_no"]):
        p = parse_report_name(r["report_nm"], acc_mt)
        if p:
            out[p] = r["rcept_no"]
    return out


async def statements(dart: DartClient, rcept_no: str, reprt_code: str) -> tuple[list[dict], str]:
    """원문 재무제표 행. 접수번호가 같으면 원문이 바뀌지 않으므로 만료 없이 캐시한다(원문 XML은 저장하지 않는다)."""
    cache = get_cache()
    key = f"v{VERSION}:{rcept_no}"
    hit = await cache.get("dart.orig", key)
    if hit is not None:
        return hit["rows"], hit["fs_div"]
    raw = await dart.binary("document.xml", rcept_no=rcept_no)
    z = zipfile.ZipFile(io.BytesIO(raw))
    main = next((n for n in z.namelist() if n.startswith(rcept_no)), z.namelist()[0])
    xml = viewer.decode(z.read(main))      # 선언은 utf-8이어도 2022년 무렵 원문은 EUC-KR인 것이 있다
    rows, fs_div = parse_statements(xml, reprt_code, rcept_no)
    await cache.set("dart.orig", key, {"rows": rows, "fs_div": fs_div})
    log.info("원문 재무제표 %s: %d행(%s)", rcept_no, len(rows), fs_div)
    return rows, fs_div
