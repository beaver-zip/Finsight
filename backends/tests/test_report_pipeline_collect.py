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
    assert "collect.index: 시세 없음" in errs                    # 지수는 늘 시세가 있다: 비면 장애
    assert "collect.history: 1개 분기 실패, 첫 오류 2Q23: DartLimit: OpenDART 020: limit" in errs


def _corp(cls):
    return CorpProfile(corp_code="c", corp_name="테스트", stock_code="123456", corp_cls=cls)


FILING = PeriodicFiling(rcept_no="r", report_nm="반기보고서 (2024.06)", rcept_dt=date(2024, 8, 14),
                        bsns_year=2024, reprt_code="11012")


@pytest.mark.asyncio
async def test_listed_stock_without_price_data_is_not_partial(sources):
    # 코넥스·거래정지 종목: yfinance가 예외 없이 빈 이력을 준다(진짜 '데이터 없음')
    sources["daily_close"].side_effect = lambda t, s: [] if t == "123456.KS" else [(date(2024, 8, 30), 2600.0)]
    out = await col.collect(None, None, _corp("N"), FILING, date(2024, 9, 1))
    assert out.px == [] and not [e for e in out.errors if e.startswith(("collect.price", "collect.index"))]


@pytest.mark.asyncio
async def test_price_fetch_failure_is_partial(sources):
    sources["daily_close"].side_effect = RuntimeError("YFRateLimitError")
    out = await col.collect(None, None, _corp("Y"), FILING, date(2024, 9, 1))
    assert "collect.price: RuntimeError: YFRateLimitError" in out.errors


@pytest.mark.asyncio
async def test_peer_price_failure_is_partial(sources, monkeypatch):
    from types import SimpleNamespace as NS

    from app.core import peers

    # 대상 회사 시세는 캐시에 있어 성공하고, 피어 시세만 레이트 리밋으로 실패하는 경우
    monkeypatch.setattr(col, "peer_rows", peers.peer_rows)
    monkeypatch.setattr(peers, "_reports", AsyncMock(return_value={}))
    monkeypatch.setattr(peers.dart_fin, "get_snapshot", AsyncMock(return_value=NS(reprt_code="11012", lines=[])))
    monkeypatch.setattr(peers, "share_counts", AsyncMock(return_value=None))
    monkeypatch.setattr(peers, "industry_group", lambda *a: "general")
    monkeypatch.setattr(peers, "peer_candidates", lambda corp, n: [
        {"corp_code": f"p{i}", "stock_code": f"00000{i}", "corp_cls": "Y", "name": f"피어{i}"} for i in range(3)])
    monkeypatch.setattr(peers, "daily_close", AsyncMock(side_effect=RuntimeError("YFRateLimitError")))
    out = await col.collect(None, None, _corp("Y"), FILING, date(2024, 9, 1))
    assert len(out.peers) == 4 and all(r.mcap is None for r in out.peers)     # 행은 재무 지표만 남는다
    assert "collect.peers: 4개 시세 실패, 첫 오류 RuntimeError: YFRateLimitError" in out.errors
