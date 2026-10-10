"""상장사 목록(피어 선정용) 만들기: 최근 3개월 정기공시 목록에서 상장사를 모으고 기업개황으로 업종코드를 붙인다.

corpCode.xml이 점검 중이어도 동작하도록 list.json(기간 3개월 제한)만 쓴다.
실행: cd backends && PYTHONPATH=. python scripts/build_universe.py [YYYYMMDD]
결과: data/universe.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta

import httpx

from app.core.dart.client import DartClient, DartNoData
from app.settings import BACKEND_DIR

OUT = BACKEND_DIR / "data" / "universe.json"


async def listed_from_filings(dart: DartClient, end: date) -> dict[str, dict]:
    bgn = end - timedelta(days=89)
    found: dict[str, dict] = {}
    for ty in ("A",):
        page = 1
        while True:
            try:
                data = await dart.json("list", bgn_de=bgn.strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                                       pblntf_ty=ty, page_no=page, page_count=100)
            except DartNoData:
                break
            for r in data.get("list", []):
                sc = (r.get("stock_code") or "").strip()
                if sc and r.get("corp_cls") in ("Y", "K"):
                    found.setdefault(r["corp_code"], {"corp_code": r["corp_code"], "stock_code": sc,
                                                      "name": r["corp_name"].strip(), "corp_cls": r["corp_cls"]})
            if page >= int(data.get("total_page", 1)):
                break
            page += 1
    return found


async def main(end: date) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        dart = DartClient(http)
        rows = await listed_from_filings(dart, end)
        print("상장사", len(rows), flush=True)
        sem = asyncio.Semaphore(4)
        done = 0

        async def one(r: dict) -> None:
            nonlocal done
            async with sem:
                try:
                    c = await dart.json("company", corp_code=r["corp_code"])
                    r["induty_code"] = (c.get("induty_code") or "").strip() or None
                    r["acc_mt"] = (c.get("acc_mt") or "").strip() or None
                    r["name"] = (c.get("stock_name") or r["name"]).strip()
                except Exception as e:  # noqa: BLE001
                    r["induty_code"] = None
                    r["error"] = str(e)[:80]
            done += 1
            if done % 200 == 0:
                print("기업개황", done, flush=True)

        await asyncio.gather(*(one(r) for r in rows.values()))
    out = sorted(rows.values(), key=lambda r: r["stock_code"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"built": datetime.now().isoformat(timespec="seconds"), "rows": out},
                              ensure_ascii=False, indent=0))
    print("저장", OUT, len(out), "업종코드 없음", sum(1 for r in out if not r.get("induty_code")), flush=True)


if __name__ == "__main__":
    d = datetime.strptime(sys.argv[1], "%Y%m%d").date() if len(sys.argv) > 1 else date.today()
    asyncio.run(main(d))
