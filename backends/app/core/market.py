"""주가·지수·분할 이력(yfinance). 동기 라이브러리라 to_thread로 감싼다."""
from __future__ import annotations

import asyncio
import logging
from datetime import date

log = logging.getLogger(__name__)
INDEX_SOURCES = {"1001": {"name": "KOSPI", "yahoo": "^KS11"}, "2001": {"name": "KOSDAQ", "yahoo": "^KQ11"}}


# ---------- 리포트 v2: 일별 종가 이력(yfinance, 분할 반영 종가) ----------
PX_TTL = 12 * 3600


def ticker_for(stock_code: str, corp_cls: str | None) -> str:
    return stock_code + (".KQ" if corp_cls == "K" else ".KS")


def index_ticker(corp_cls: str | None) -> tuple[str, str]:
    return ("^KQ11", "KOSDAQ") if corp_cls == "K" else ("^KS11", "KOSPI")


def _yf_daily(ticker: str, start: str) -> list[list]:
    import yfinance as yf

    h = yf.Ticker(ticker).history(start=start, auto_adjust=False)
    if h is None or h.empty:
        return []
    return [[i.date().isoformat(), float(v)] for i, v in h["Close"].items() if v == v]


def _yf_splits(ticker: str) -> list[list]:
    import yfinance as yf

    s = yf.Ticker(ticker).splits
    return [[i.date().isoformat(), float(v)] for i, v in s.items()] if s is not None else []


async def daily_close(ticker: str, start: date) -> list[tuple[date, float]]:
    """start 이후 일별 종가. 12시간 캐시."""
    from app.core.cache import get_cache, make_key

    cache = get_cache()
    key = make_key(ticker, start.isoformat(), date.today().isoformat())
    hit = await cache.get("px", key)
    if hit is None:
        try:
            hit = await asyncio.to_thread(_yf_daily, ticker, start.isoformat())
        except Exception as e:  # noqa: BLE001
            log.warning("시세 실패 %s: %s", ticker, e)
            hit = []
        if hit:
            await cache.set("px", key, hit, ttl_s=PX_TTL)
    return [(date.fromisoformat(d), v) for d, v in hit]


async def splits(ticker: str) -> list[tuple[date, float]]:
    from app.core.cache import get_cache

    cache = get_cache()
    hit = await cache.get("px.split", ticker)
    if hit is None:
        try:
            hit = await asyncio.to_thread(_yf_splits, ticker)
        except Exception as e:  # noqa: BLE001
            log.info("분할 이력 실패 %s: %s", ticker, e)
            hit = []
        await cache.set("px.split", ticker, hit, ttl_s=86_400)
    return [(date.fromisoformat(d), v) for d, v in hit]


def pref_candidates(stock_code: str) -> list[str]:
    """보통주 코드의 끝자리를 바꾼 우선주 후보(예: 005930 → 005935)."""
    base = stock_code[:-1]
    return [base + d for d in ("5", "7", "9")]
