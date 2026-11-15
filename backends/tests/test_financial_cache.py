"""실제 SQLite 캐시와 DART 대역으로 재무 캐시 갱신·이력 계산을 검증한다."""
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.core import cache as cache_module
from app.core.dart import financials, history
from app.core.dart.client import DartNoData


def rows(receipt="old", amount="100"):
    return [{"rcept_no": receipt, "sj_div": "IS", "account_id": "ifrs-full_Revenue",
             "account_nm": "매출액", "thstrm_amount": amount, "thstrm_add_amount": amount}]


@pytest.mark.asyncio
async def test_snapshot_reuses_matching_cache_and_refreshes_correction():
    dart = NS(json=AsyncMock(side_effect=[{"list": rows()}, {"list": rows("corrected", "150")}]))
    first = await financials.get_snapshot(dart, "corp", 2024, "11013", expected_rcept_no="old")
    assert await financials.get_snapshot(dart, "corp", 2024, "11013", expected_rcept_no="old") == first
    assert dart.json.await_count == 1
    corrected = await financials.get_snapshot(dart, "corp", 2024, "11013", expected_rcept_no="corrected")
    assert corrected.rcept_no == "corrected" and corrected.lines[0].thstrm_amount == Decimal("150")
    assert dart.json.await_count == 2


@pytest.mark.parametrize("no_data", [True, False])
@pytest.mark.asyncio
async def test_empty_response_expires_then_new_data_is_fetched(monkeypatch, no_data):
    now = cache_module.time.time()
    monkeypatch.setattr(cache_module.time, "time", lambda: now)
    empty = DartNoData("013", "no data") if no_data else {"list": []}
    dart = NS(json=AsyncMock(side_effect=[empty, empty, {"list": rows("published")}]))
    assert await financials.get_snapshot(dart, "corp", 2024, "11013") is None
    now += 299
    assert await financials.get_snapshot(dart, "corp", 2024, "11013") is None
    assert dart.json.await_count == 2
    now += 2
    snap = await financials.get_snapshot(dart, "corp", 2024, "11013")
    assert snap.rcept_no == "published" and dart.json.await_count == 3


@pytest.mark.asyncio
async def test_positive_cache_expires_without_expected_receipt_and_isolates_keys(monkeypatch):
    now = cache_module.time.time()
    monkeypatch.setattr(cache_module.time, "time", lambda: now)
    dart = NS(json=AsyncMock(side_effect=[{"list": rows()}, {"list": rows("other")},
                                        {"list": rows("period")}, {"list": rows("updated")}]))
    await financials.get_snapshot(dart, "corp", 2024, "11013")
    now += 100
    await financials.get_snapshot(dart, "other", 2024, "11013")
    await financials.get_snapshot(dart, "corp", 2024, "11012")
    now += 3501
    snap = await financials.get_snapshot(dart, "corp", 2024, "11013")
    assert snap.rcept_no == "updated"
    assert (await financials.get_snapshot(dart, "other", 2024, "11013")).rcept_no == "other"
    assert (await financials.get_snapshot(dart, "corp", 2024, "11012")).rcept_no == "period"
    assert dart.json.await_count == 4


@pytest.mark.parametrize("legacy", [[], rows()])
@pytest.mark.asyncio
async def test_legacy_permanent_entries_are_not_reused(temp_cache, legacy):
    for fs in ("CFS", "OFS"):
        await temp_cache.set("dart.fin", f"corp:2024:11013:{fs}", legacy)
    dart = NS(json=AsyncMock(return_value={"list": rows("published")}))
    assert (await financials.get_snapshot(dart, "corp", 2024, "11013")).rcept_no == "published"


@pytest.mark.parametrize("entrypoint", ["quarterly", "cumulative"])
@pytest.mark.asyncio
async def test_history_refreshes_corrected_period_before_ttl(entrypoint, monkeypatch):
    dart = NS(json=AsyncMock(return_value={"list": rows()}))
    await financials.get_snapshot(dart, "corp", 2024, "11013")
    reports = AsyncMock(return_value={(2024, "11013"): "corrected"})
    monkeypatch.setattr(history.original, "periodic_reports", reports)
    dart.json.return_value = {"list": rows("corrected", "150")}
    if entrypoint == "quarterly":
        result = await history.quarterly_history(dart, "corp", (2024, 1), first_year=2024, use_original=False)
        assert result.quarters[0].rcept_no == "corrected"
        assert result.quarters[0].revenue == Decimal("150")
    else:
        result = await history.cumulative_items(dart, "corp", [(2024, 1)])
        assert result[(2024, 1)]["rcept_no"] == "corrected"
        assert result[(2024, 1)]["revenue"] == Decimal("150")
    reports.assert_awaited_once_with(dart, "corp", 2024, 12)


@pytest.mark.asyncio
async def test_fallback_to_ofs_then_new_cfs_after_empty_cache_expires(monkeypatch):
    now = cache_module.time.time()
    monkeypatch.setattr(cache_module.time, "time", lambda: now)
    dart = NS(json=AsyncMock(side_effect=[DartNoData("013", "no CFS"), {"list": rows("separate")},
                                        {"list": rows("consolidated")}]))
    assert (await financials.get_snapshot(dart, "corp", 2024, "11013")).fs_div == "OFS"
    now += 301
    snap = await financials.get_snapshot(dart, "corp", 2024, "11013")
    assert snap.fs_div == "CFS" and snap.rcept_no == "consolidated"
    assert [c.kwargs["fs_div"] for c in dart.json.await_args_list] == ["CFS", "OFS", "CFS"]


@pytest.mark.asyncio
async def test_refresh_error_does_not_overwrite_cached_data():
    from app.core.dart.client import DartError

    dart = NS(json=AsyncMock(return_value={"list": rows()}))
    old = await financials.get_snapshot(dart, "corp", 2024, "11013")
    dart.json.side_effect = DartError("800", "maintenance")
    with pytest.raises(DartError):
        await financials.get_snapshot(dart, "corp", 2024, "11013", expected_rcept_no="corrected")
    assert await financials.get_snapshot(dart, "corp", 2024, "11013") == old


@pytest.mark.asyncio
async def test_upstream_lag_does_not_relabel_old_receipt_and_retries_after_ttl(monkeypatch):
    now = cache_module.time.time()
    monkeypatch.setattr(cache_module.time, "time", lambda: now)
    dart = NS(json=AsyncMock(return_value={"list": rows()}))
    snap = await financials.get_snapshot(dart, "corp", 2024, "11013", expected_rcept_no="corrected")
    assert snap.rcept_no == "old"
    dart.json.return_value = {"list": rows("corrected", "150")}
    # 재무 API가 계속 옛 번호를 주는 정정(첨부정정 등)도 1시간 동안은 다시 받지 않는다
    now += 3599
    assert (await financials.get_snapshot(dart, "corp", 2024, "11013", expected_rcept_no="corrected")).rcept_no == "old"
    assert dart.json.await_count == 1
    now += 2
    assert (await financials.get_snapshot(dart, "corp", 2024, "11013", expected_rcept_no="corrected")).rcept_no == "corrected"


@pytest.mark.asyncio
async def test_history_uses_real_report_mapping_for_multiple_periods():
    async def json(endpoint, **params):
        if endpoint == "list":
            return {"total_page": 1, "list": [
                {"report_nm": "분기보고서 (2024.03)", "rcept_no": "20240501000001"},
                {"report_nm": "[기재정정]분기보고서 (2024.03)", "rcept_no": "20240801000001"},
                {"report_nm": "반기보고서 (2024.06)", "rcept_no": "20240815000001"}]}
        receipt = "20240801000001" if params["reprt_code"] == "11013" else "20240815000001"
        return {"list": rows(receipt, "200")}

    dart = NS(json=AsyncMock(side_effect=json))
    result = await history.quarterly_history(dart, "corp", (2024, 2), first_year=2024, use_original=False)
    assert [r.rcept_no for r in result.quarters] == ["20240801000001", "20240815000001"]
    calls = dart.json.await_count
    again = await history.quarterly_history(dart, "corp", (2024, 2), first_year=2024, use_original=False)
    assert again == result and dart.json.await_count == calls


@pytest.mark.asyncio
async def test_report_list_outage_keeps_available_financial_history(monkeypatch):
    monkeypatch.setattr(history.original, "periodic_reports", AsyncMock(side_effect=RuntimeError("offline")))
    dart = NS(json=AsyncMock(return_value={"list": rows()}))
    result = await history.quarterly_history(dart, "corp", (2024, 1), first_year=2024, use_original=False)
    assert result.quarters[0].revenue == Decimal("100")


@pytest.mark.asyncio
async def test_quarter_outage_keeps_other_quarters_and_is_reported(monkeypatch):
    from app.core.dart.client import DartLimit

    monkeypatch.setattr(history.original, "periodic_reports", AsyncMock(return_value={}))

    async def json(endpoint, **params):
        if params["reprt_code"] == "11012":
            raise DartLimit("020", "limit")
        return {"list": rows()}

    dart = NS(json=AsyncMock(side_effect=json))
    result = await history.quarterly_history(dart, "corp", (2024, 2), first_year=2024, use_original=False)
    assert result.quarters[0].revenue == Decimal("100") and result.quarters[1].revenue is None
    assert result.errors == ["2Q24: DartLimit: OpenDART 020: limit"]


@pytest.mark.asyncio
async def test_cumulative_reuses_supplied_snapshots_without_external_calls(monkeypatch):
    dart = NS(json=AsyncMock(return_value={"list": rows()}))
    snap = await financials.get_snapshot(dart, "corp", 2024, "11013")
    reports = AsyncMock(side_effect=AssertionError("unexpected list fetch"))
    monkeypatch.setattr(history.original, "periodic_reports", reports)
    result = await history.cumulative_items(dart, "corp", [(2024, 1)], snapshots={(2024, 1): snap})
    assert result[(2024, 1)]["revenue"] == Decimal("100")
    reports.assert_not_awaited()
    assert dart.json.await_count == 1
    assert await history.cumulative_items(dart, "corp", []) == {}
