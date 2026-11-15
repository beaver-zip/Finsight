"""시세: 신형 우선주 후보, 없는 티커는 짧게 기억해 리포트마다 다시 묻지 않는다."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.core import market, peers


def test_pref_candidates_include_new_style_code():
    assert "02826K" in market.pref_candidates("028260")             # 삼성물산우B
    assert market.pref_candidates("005930")[0] == "005935"


async def test_daily_close_remembers_empty_answer_briefly(monkeypatch, temp_cache):
    calls = []
    monkeypatch.setattr(market, "_yf_daily", lambda t, s: calls.append(t) or [])
    for _ in range(2):
        assert await market.daily_close("028265.KS", date(2026, 11, 1)) == []
    assert calls == ["028265.KS"]
    exp = temp_cache._run("SELECT expires_at - created_at FROM kv WHERE namespace='px'")
    assert exp and exp[0][0] <= 3600                               # 일시 장애면 1시간 뒤 다시 묻는다


async def test_daily_close_failure_is_raised_and_not_cached(monkeypatch, temp_cache):
    calls = []

    def flaky(ticker, start):
        calls.append(ticker)
        if len(calls) == 1:
            raise RuntimeError("YFRateLimitError: Too Many Requests")
        return [["2026-11-13", 70000.0]]

    monkeypatch.setattr(market, "_yf_daily", flaky)
    with pytest.raises(RuntimeError):
        await market.daily_close("005935.KS", date(2026, 11, 1))
    assert await market.daily_close("005935.KS", date(2026, 11, 1)) == [(date(2026, 11, 13), 70000.0)]
    assert len(calls) == 2                                          # 실패는 1시간 동안 []로 남지 않는다


async def test_peer_row_survives_price_outage(monkeypatch):
    snap = NS(reprt_code="11011", lines=[])
    monkeypatch.setattr(peers.dart_fin, "get_snapshot", AsyncMock(return_value=snap))
    monkeypatch.setattr(peers, "share_counts", AsyncMock(return_value=None))
    monkeypatch.setattr(peers, "industry_group", lambda *a: "general")
    monkeypatch.setattr(peers, "daily_close", AsyncMock(side_effect=RuntimeError("yfinance down")))
    row = await peers.metrics_for(None, "c", "005930", "Y", "테스트", 2025, 4)
    assert row is not None and row.price is None and row.mcap is None
