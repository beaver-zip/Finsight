"""기업 식별: corpCode.xml(1일 캐시)로 기업명·종목코드 → corp_code, company.json으로 기업개황."""
from __future__ import annotations

import io
import time
import zipfile
from xml.etree import ElementTree as ET

from app.core.cache import get_cache
from app.core.dart.client import DartClient, cached
from app.domain.models import CorpProfile

DAY = 86_400
MEMO_S = 600
# (캐시, 읽은 시각, 목록, 정규화 이름): 약 11만 줄을 조회마다 다시 읽고 정규화하지 않는다
_mem: tuple[object, float, list[dict], list[str]] | None = None


async def corp_list(dart: DartClient) -> list[dict]:
    """[{corp_code, corp_name, stock_code}] 전체 목록. OpenDART가 실패하면 만료된 목록이라도 쓴다."""
    return (await _index(dart))[0]


async def _index(dart: DartClient) -> tuple[list[dict], list[str]]:
    """(목록, 정규화 이름). SQLite 캐시가 원본이고, 같은 캐시에서 MEMO_S 안에 다시 부르면 메모리의 값을 쓴다."""
    global _mem
    cache = get_cache()
    if _mem and _mem[0] is cache and time.monotonic() - _mem[1] < MEMO_S:
        return _mem[2], _mem[3]

    async def fetch() -> list[dict]:
        raw = await dart.binary("corpCode.xml")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xml = z.read(z.namelist()[0])
        rows = []
        for el in ET.fromstring(xml).iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            rows.append({"corp_code": el.findtext("corp_code"), "corp_name": (el.findtext("corp_name") or "").strip(),
                         "corp_eng_name": (el.findtext("corp_eng_name") or "").strip(),
                         "modify_date": (el.findtext("modify_date") or "").strip(), "stock_code": stock or None})
        return rows

    rows = await cached("dart.corplist", "all", DAY, fetch)
    _mem = (cache, time.monotonic(), rows, [_norm(r["corp_name"]) for r in rows])
    return _mem[2], _mem[3]


def _norm(s: str) -> str:
    return s.replace(" ", "").replace("(주)", "").replace("㈜", "").lower()


async def find_candidates(dart: DartClient, query: str, limit: int = 5) -> list[dict]:
    """라우터 규칙: 이름이 질의어와 정확히 같은 회사들, 없으면 이름에 질의어가 든 회사들(상장사 먼저, 최대 5개).
    후보에 상장사가 있으면 상장사만 남긴다. 6자리 숫자면 종목코드, 8자리면 corp_code로 찾는다."""
    rows, names = await _index(dart)
    q = query.strip()
    if q.isdigit() and len(q) == 8:
        exact = [r for r in rows if r["corp_code"] == q]
    elif q.isdigit() and len(q) == 6:
        exact = [r for r in rows if r["stock_code"] == q]
    else:
        nq = _norm(q)
        exact = [r for r, n in zip(rows, names) if n == nq]
        if not exact:
            partial = [r for r, n in zip(rows, names) if nq and nq in n]
            partial.sort(key=lambda r: (r["stock_code"] is None, len(r["corp_name"])))
            exact = partial[:limit]
    listed = [r for r in exact if r["stock_code"]]
    return (listed or exact)[:limit]


async def get_profile(dart: DartClient, corp_code: str) -> CorpProfile:
    hit = await cached("dart.company", corp_code, DAY, lambda: dart.json("company", corp_code=corp_code))
    acc = (hit.get("acc_mt") or "").strip()
    return CorpProfile(corp_code=corp_code, corp_name=hit.get("corp_name") or hit.get("stock_name") or corp_code,
                       stock_code=(hit.get("stock_code") or "").strip() or None, corp_cls=hit.get("corp_cls"),
                       induty_code=hit.get("induty_code"), acc_mt=int(acc) if acc.isdigit() else None)


async def search_keyword(dart: DartClient, keyword: str, limit: int = 50) -> list[dict]:
    """/financial/corp_list용: 이름에 키워드가 든 회사(상장사·짧은 이름 먼저)."""
    rows, names = await _index(dart)
    nq = _norm(keyword)
    if not nq:
        return []
    hits = [r for r, n in zip(rows, names) if nq in n]
    hits.sort(key=lambda r: (_norm(r["corp_name"]) != nq, r["stock_code"] is None, len(r["corp_name"])))
    return hits[:limit]
