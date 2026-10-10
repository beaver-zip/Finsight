"""universe.json에 대략적 시가총액(보통주 종가 × 보통주 유통주식수)을 붙인다. 피어 후보를 크기순으로 고르는 데만 쓴다.
실행: cd backends && PYTHONPATH=. python scripts/add_universe_mcap.py 2026 11012
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

from app.core.dart.client import DartClient
from app.core.dart.shares import share_counts
from app.settings import BACKEND_DIR

PATH = BACKEND_DIR / "data" / "universe.json"


def prices(tickers: list[str]) -> dict[str, float]:
    import yfinance as yf

    out = {}
    for i in range(0, len(tickers), 200):
        chunk = tickers[i:i + 200]
        df = yf.download(chunk, period="5d", auto_adjust=False, progress=False, threads=True, group_by="ticker")
        for t in chunk:
            try:
                s, v = df[t]["Close"].dropna(), df[t]["Volume"].fillna(0)
                # 거래정지 종목은 야후 시세가 깨져 있는 경우가 있다(거래량 0, 비정상 가격)
                if len(s) and v.sum() > 0 and 0 < s.iloc[-1] < 1e7:
                    out[t] = float(s.iloc[-1])
            except Exception:  # noqa: BLE001
                pass
    return out


async def main(year: int, rc: str) -> None:
    data = json.loads(PATH.read_text())
    rows = data["rows"]
    async with httpx.AsyncClient(timeout=30) as http:
        dart = DartClient(http)
        sem = asyncio.Semaphore(4)
        done = 0

        async def one(r: dict) -> None:
            nonlocal done
            async with sem:
                sh = None
                for y, c in ((year, rc), (year - 1, "11011")):
                    try:
                        sh = await share_counts(dart, r["corp_code"], y, c)
                    except Exception:  # noqa: BLE001
                        sh = None
                    if sh:
                        break
                r["common_out"] = sh.common_out if sh else None
            done += 1
            if done % 300 == 0:
                print("주식수", done, flush=True)

        await asyncio.gather(*(one(r) for r in rows))
    tick = {r["stock_code"]: r["stock_code"] + (".KQ" if r["corp_cls"] == "K" else ".KS") for r in rows}
    px = await asyncio.to_thread(prices, list(tick.values()))
    for r in rows:
        p = px.get(tick[r["stock_code"]])
        r["mcap_approx"] = round(p * r["common_out"] / 1e8) if p and r.get("common_out") else None   # 억원
    data["mcap_basis"] = f"{year}:{rc} 보통주 유통주식수 × 최근 종가(억원)"
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=0))
    print("저장", sum(1 for r in rows if r.get("mcap_approx")), "/", len(rows), flush=True)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
