"""주가·지수(pykrx, 지수 실패 시 yfinance). 동기 라이브러리라 to_thread로 감싼다."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)
INDEX_SOURCES = {"1001": {"name": "KOSPI", "yahoo": "^KS11"}, "2001": {"name": "KOSDAQ", "yahoo": "^KQ11"}}


def _index_close(index_code: str, start: str, end: str):
    import pandas as pd

    meta = INDEX_SOURCES[index_code]
    try:
        from pykrx import stock

        df = stock.get_index_ohlcv(start, end, index_code, name_display=False)
        if df is not None and not df.empty and "종가" in df:
            s = df["종가"]
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            return s.rename(meta["name"])
        raise ValueError("pykrx 지수 응답이 비어 있다")
    except Exception as e:  # noqa: BLE001
        log.info("%s pykrx 실패, yfinance로 재시도: %s", meta["name"], e)
    try:
        import yfinance as yf

        h = yf.Ticker(meta["yahoo"]).history(start=f"{start[:4]}-{start[4:6]}-{start[6:]}",
                                             end=f"{end[:4]}-{end[4:6]}-{end[6:]}", auto_adjust=False)
        if h is None or h.empty:
            return None
        s = h["Close"]
        s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
        return s.rename(meta["name"])
    except Exception as e:  # noqa: BLE001
        log.info("%s yfinance 실패: %s", meta["name"], e)
        return None


def _stock_close(stock_code: str, corp_cls: str | None, start: str, end: str):
    """pykrx 종가, 비어 있거나 실패하면 yfinance(유가 .KS, 코스닥 .KQ)."""
    import pandas as pd

    try:
        from pykrx import stock

        df = stock.get_market_ohlcv(start, end, stock_code)
        if df is not None and not df.empty and "종가" in df:
            s = df["종가"]
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            return s
        raise ValueError("pykrx 주가 응답이 비어 있다")
    except Exception as e:  # noqa: BLE001
        log.info("pykrx 주가 실패 %s, yfinance로 재시도: %s", stock_code, e)
    import yfinance as yf

    suffix = ".KQ" if corp_cls == "K" else ".KS"
    h = yf.Ticker(stock_code + suffix).history(start=f"{start[:4]}-{start[4:6]}-{start[6:]}",
                                               end=f"{end[:4]}-{end[4:6]}-{end[6:]}", auto_adjust=False)
    if h is None or h.empty:
        return None
    s = h["Close"]
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s


def _price_vs_index(stock_code: str, name: str, today: date, corp_cls: str | None = None) -> dict | None:
    start, end = (today - timedelta(days=365)).strftime("%Y%m%d"), today.strftime("%Y%m%d")
    close = _stock_close(stock_code, corp_cls, start, end)
    if close is None or close.empty:
        return None
    merged = close.rename(name).to_frame()
    for code in INDEX_SOURCES:
        s = _index_close(code, start, end)
        if s is not None:
            merged = merged.join(s, how="inner")
    if merged.empty:
        return None
    norm = merged / merged.iloc[0] * 100
    names = list(merged.columns)
    return {"title": f"{name} 주가" + (" vs 주요 지수" if len(names) > 1 else "") + " (최근 1년, 첫 거래일=100)",
            "x_values": norm.index.strftime("%Y-%m-%d").tolist(),
            "traces": [{"name": n, "y_values": norm[n].round(2).tolist(), "custom_data": merged[n].tolist()}
                       for n in names]}


async def price_vs_index(stock_code: str, name: str, today: date | None = None,
                         corp_cls: str | None = None) -> dict | None:
    """최근 1년 일별 종가를 첫 거래일 = 100으로 맞춘 차트 데이터. 실패하면 None."""
    try:
        return await asyncio.to_thread(_price_vs_index, stock_code, name, today or date.today(), corp_cls)
    except Exception as e:  # noqa: BLE001
        log.warning("주가 차트 데이터 실패 %s: %s", stock_code, e)
        return None
