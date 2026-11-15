"""시세: 신형 우선주 후보, 없는 티커는 짧게 기억해 리포트마다 다시 묻지 않는다."""
from __future__ import annotations

from datetime import date

from app.core import market


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
