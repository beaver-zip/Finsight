"""기업 식별: corpCode.xml(1일 캐시)로 기업명·종목코드 → corp_code, company.json으로 기업개황."""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from app.core.cache import get_cache
from app.core.dart.client import DartClient
from app.domain.models import CorpProfile

DAY = 86_400


async def corp_list(dart: DartClient) -> list[dict]:
    """[{corp_code, corp_name, stock_code}] 전체 목록."""
    cache = get_cache()
    hit = await cache.get("dart.corplist", "all")
    if hit is not None:
        return hit
    raw = await dart.binary("corpCode.xml")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(z.namelist()[0])
    rows = []
    for el in ET.fromstring(xml).iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        rows.append({"corp_code": el.findtext("corp_code"), "corp_name": (el.findtext("corp_name") or "").strip(),
                     "corp_eng_name": (el.findtext("corp_eng_name") or "").strip(),
                     "modify_date": (el.findtext("modify_date") or "").strip(), "stock_code": stock or None})
    await cache.set("dart.corplist", "all", rows, ttl_s=DAY)
    return rows


def _norm(s: str) -> str:
    return s.replace(" ", "").replace("(주)", "").replace("㈜", "").lower()


async def find_candidates(dart: DartClient, query: str, limit: int = 5) -> list[dict]:
    """라우터 규칙: 이름이 질의어와 정확히 같은 회사들, 없으면 이름에 질의어가 든 회사들(상장사 먼저, 최대 5개).
    후보에 상장사가 있으면 상장사만 남긴다. 6자리 숫자면 종목코드, 8자리면 corp_code로 찾는다."""
    rows = await corp_list(dart)
    q = query.strip()
    if q.isdigit() and len(q) == 8:
        exact = [r for r in rows if r["corp_code"] == q]
    elif q.isdigit() and len(q) == 6:
        exact = [r for r in rows if r["stock_code"] == q]
    else:
        nq = _norm(q)
        exact = [r for r in rows if _norm(r["corp_name"]) == nq]
        if not exact:
            partial = [r for r in rows if nq and nq in _norm(r["corp_name"])]
            partial.sort(key=lambda r: (r["stock_code"] is None, len(r["corp_name"])))
            exact = partial[:limit]
    listed = [r for r in exact if r["stock_code"]]
    return (listed or exact)[:limit]


async def get_profile(dart: DartClient, corp_code: str) -> CorpProfile:
    cache = get_cache()
    hit = await cache.get("dart.company", corp_code)
    if hit is None:
        hit = await dart.json("company", corp_code=corp_code)
        await cache.set("dart.company", corp_code, hit, ttl_s=DAY)
    acc = (hit.get("acc_mt") or "").strip()
    return CorpProfile(corp_code=corp_code, corp_name=hit.get("corp_name") or hit.get("stock_name") or corp_code,
                       stock_code=(hit.get("stock_code") or "").strip() or None, corp_cls=hit.get("corp_cls"),
                       induty_code=hit.get("induty_code"), acc_mt=int(acc) if acc.isdigit() else None)


async def search_keyword(dart: DartClient, keyword: str, limit: int = 50) -> list[dict]:
    """/financial/corp_list용: 이름에 키워드가 든 회사(상장사·짧은 이름 먼저)."""
    rows = await corp_list(dart)
    nq = _norm(keyword)
    if not nq:
        return []
    hits = [r for r in rows if nq in _norm(r["corp_name"])]
    hits.sort(key=lambda r: (_norm(r["corp_name"]) != nq, r["stock_code"] is None, len(r["corp_name"])))
    return hits[:limit]
