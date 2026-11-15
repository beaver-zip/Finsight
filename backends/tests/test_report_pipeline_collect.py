"""collect가 외부 수집 실패를 errors에 남기는지(조용히 빈 값으로 바꾸지 않는지)."""
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.agents.company import collect as col
from app.core.dart.client import DartLimit
from app.core.dart.history import History, QRow
from app.domain.models import CorpProfile, PeriodicFiling


@pytest.fixture
def sources(monkeypatch):
    hist = History(quarters=[QRow(key="1Q24", year=2024, q=1), QRow(key="2Q24", year=2024, q=2)],
                   errors=["2Q23: DartLimit: OpenDART 020: limit"])
    fakes = {"quarterly_history": AsyncMock(return_value=hist), "daily_close": AsyncMock(return_value=[]),
             "splits": AsyncMock(return_value=[]), "latest_prelim": AsyncMock(return_value=None),
             "prelim_filings": AsyncMock(return_value=[]), "buyback_decisions": AsyncMock(return_value=[]),
             "peer_rows": AsyncMock(return_value=[]), "cumulative_items": AsyncMock(return_value={}),
             "share_counts": AsyncMock(side_effect=DartLimit("020", "limit")),
             "dividend": AsyncMock(side_effect=TimeoutError("slow")),
             "segment_cumulative": AsyncMock(return_value=None)}
    for name, fake in fakes.items():
        monkeypatch.setattr(col, name, fake)
    return fakes


@pytest.mark.asyncio
async def test_collect_records_swallowed_source_failures(sources):
    corp = CorpProfile(corp_code="c", corp_name="테스트", stock_code="005930", corp_cls="Y")
    filing = PeriodicFiling(rcept_no="r", report_nm="반기보고서 (2024.06)", rcept_dt=date(2024, 8, 14),
                            bsns_year=2024, reprt_code="11012")
    out = await col.collect(None, None, corp, filing, date(2024, 9, 1))
    assert out.shares == {} and out.dividends == {}
    errs = out.errors
    assert "collect.shares: 2건 실패, 첫 오류 DartLimit: OpenDART 020: limit" in errs
    assert "collect.dividends: 3건 실패, 첫 오류 TimeoutError: slow" in errs
    assert "collect.price: 시세 없음" in errs and "collect.index: 시세 없음" in errs
    assert "collect.history: 1개 분기 실패, 첫 오류 2Q23: DartLimit: OpenDART 020: limit" in errs
