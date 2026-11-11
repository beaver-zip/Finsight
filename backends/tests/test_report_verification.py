"""실제 기업 그래프·보고서 API의 검증 실패 처리와 캐시 정책."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.company import nodes, writer
from app.agents.company.analysis import ReportData
from app.agents.company.graph import build_company_graph
from app.api import report
from app.core import budget
from app.deps import get_state
from app.domain.models import Block, CorpProfile, Headline, ReportDraft, VerificationIssue, VerificationResult


@pytest.fixture
def scenario(monkeypatch):
    data = ReportData(corp={"name": "테스트 기업"}, made="2026-11-11", asof=None, latest={},
                      table_div=1e8, table_unit="억원", key_data=[{"label": "계산된 수치", "value": "100"}])
    draft = ReportDraft(**{h: Headline(text="미검증 제목") for h in ReportDraft.HEADS},
                        **{g: [] for g in ReportDraft.GROUPS})
    draft.summary = [Block(id="summary.1", head="문제 주장", text="제거할 문장"),
                     Block(id="summary.2", head="통과 주장", text="남길 문장")]

    async def empty(state, config):
        return {}

    async def analyze(state, config):
        return {"data": data}

    for name in ("get_filing", "collect_data", "get_disclosures", "get_news", "regulatory_check"):
        monkeypatch.setattr(nodes, name, empty)
    monkeypatch.setattr(nodes, "analyze_data", analyze)
    monkeypatch.setattr(writer, "write_draft", AsyncMock(return_value=draft))
    monkeypatch.setattr(writer, "revise", AsyncMock(return_value=draft))
    monkeypatch.setattr(writer, "style_issues", lambda *args: [])
    verify = AsyncMock(return_value=VerificationResult(passed=True))
    monkeypatch.setattr(nodes, "verify", verify)
    profile = CorpProfile(corp_code="test", corp_name="테스트 기업")
    cache_key = report._cache_key
    monkeypatch.setattr(report, "_cache_key", AsyncMock(return_value=(profile, f"{report.VERSION}:test")))
    # 폰트 서브셋 생성은 검증 정책과 무관하다.
    monkeypatch.setattr("app.agents.company.render.font_faces", lambda _: "")
    st = NS(company_graph=build_company_graph(), dart=None, http=None, legal_runner=None)
    return NS(st=st, verify=verify, draft=draft, data=data, profile=profile, cache_key=cache_key)


def configure(s, mode):
    if mode == "exception":
        s.verify.side_effect = RuntimeError("injected verifier outage")
    elif mode == "token":
        def exceeded(*args, **kwargs):
            from app.settings import get_settings
            budget.add_request_usage("claude", get_settings().request_token_budget, 0)
            budget.check_request_tokens()
        s.verify.side_effect = exceeded
    elif mode == "failed":
        s.verify.return_value = VerificationResult(passed=False, issues=[
            VerificationIssue(claim_id="summary.1", type="unsupported", detail="no evidence")])


@pytest.mark.parametrize("mode", ["exception", "failed", "passed"])
@pytest.mark.asyncio
async def test_real_graph_finalizes_and_warns(scenario, mode):
    s = scenario
    configure(s, mode)
    out = await s.st.company_graph.ainvoke({"corp": s.profile, "errors": []})
    assert "계산된 수치" in out["html"]
    if mode == "exception":
        assert out["draft"] is None
        assert "제거할 문장" not in out["html"] and "미검증 제목" not in out["html"]
        assert "검증을 완료하지 못해" in out["html"]
        assert out["verification"] is None and out["errors"]
    elif mode == "failed":
        assert "제거할 문장" not in out["html"] and "남길 문장" in out["html"]
        assert "검증을 통과하지 못한" in out["html"]
    else:
        assert out["draft"] == s.draft and "남길 문장" in out["html"]
        assert not out["errors"]


@pytest.mark.parametrize("route", ["html", "json", "stream"])
@pytest.mark.parametrize("mode", ["exception", "failed", "passed", "token"])
def test_api_only_caches_verified_reports(scenario, route, mode):
    s = scenario
    configure(s, mode)
    app = FastAPI()
    app.include_router(report.router, prefix="/report")
    app.dependency_overrides[get_state] = lambda: s.st
    path = "/report/test" + (f"/{route}" if route != "html" else "")
    with TestClient(app) as client:
        first = client.get(path)
        assert first.status_code == 200
        if route == "json":
            body = first.json()
            assert body["data"]["key_data"] and (body["draft"] is None) == (mode in ("exception", "token"))
            assert bool(body["errors"]) == (mode != "passed")
        elif mode != "passed":
            assert "검증" in first.text
            if route == "stream":
                assert "event: error" in first.text and '"partial": true' in first.text
            assert "제거할 문장" not in first.text
        else:
            assert "남길 문장" in first.text
        calls = s.verify.await_count
        second = client.get(path)
        assert second.status_code == 200
        assert (s.verify.await_count == calls) == (mode == "passed")


@pytest.mark.asyncio
async def test_verifier_outage_after_revision_discards_rewritten_draft(scenario):
    s = scenario
    s.verify.side_effect = [VerificationResult(passed=False, issues=[
        VerificationIssue(claim_id="summary.1", type="unsupported", detail="no evidence")]),
        RuntimeError("outage after revision")]
    out = await s.st.company_graph.ainvoke({"corp": s.profile, "errors": []})
    assert out["attempts"] == 1 and out["draft"] is None and out["verification"] is None
    assert "남길 문장" not in out["html"] and "미검증 제목" not in out["html"]
    assert "계산된 수치" in out["html"] and "검증을 완료하지 못해" in out["html"]


@pytest.mark.asyncio
async def test_legacy_cached_report_is_not_served(scenario, monkeypatch, temp_cache):
    s = scenario
    monkeypatch.setattr(report, "_cache_key", s.cache_key)
    monkeypatch.setattr(report.dart_corp, "get_profile", AsyncMock(return_value=s.profile))
    monkeypatch.setattr(report.dart_filings, "latest_periodic", AsyncMock(return_value=None))
    await temp_cache.set("report", "v2.3:test:none:-", {"html": "unverified legacy report", "json": {}})
    result = await report.build_report(s.st, "test")
    assert "unverified legacy report" not in result["html"]
    assert s.verify.await_count == 1


def test_html_json_stream_share_verified_cache(scenario):
    app = FastAPI()
    app.include_router(report.router, prefix="/report")
    app.dependency_overrides[get_state] = lambda: scenario.st
    with TestClient(app) as client:
        assert client.get("/report/test/json").json()["verification"]["passed"] is True
        assert "남길 문장" in client.get("/report/test").text
        stream = client.get("/report/test/stream").text
        assert '"cached": true' in stream and "남길 문장" in stream
    assert scenario.verify.await_count == 1


@pytest.mark.asyncio
async def test_nested_report_usage_is_included_in_parent_and_cached_cost_stays_local(scenario, monkeypatch):
    import asyncio

    parent = budget.start_request()
    budget.add_request_usage("anthropic", 10, 0.1)
    invoke = scenario.st.company_graph.ainvoke

    async def accounted(*args, **kwargs):
        await asyncio.sleep(0)
        budget.add_request_usage("anthropic", 20, 0.2)
        return await invoke(*args, **kwargs)

    monkeypatch.setattr(scenario.st.company_graph, "ainvoke", accounted)

    async def sibling():
        budget.add_request_usage("tavily", 0, 0.3)

    result, _ = await asyncio.gather(report.build_report(scenario.st, "test"), sibling())
    assert parent.tokens == 30 and parent.cost_usd == pytest.approx(0.6)
    assert parent.calls == 3 and parent.by_service == pytest.approx({"anthropic": 0.3, "tavily": 0.3})
    assert result["json"]["cost_usd"] == pytest.approx(0.2)
    await report.build_report(scenario.st, "test")
    assert parent.cost_usd == pytest.approx(0.6)
    budget.add_request_usage("anthropic", 5, 0.1)
    assert parent.tokens == 35
