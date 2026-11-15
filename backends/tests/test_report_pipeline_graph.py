"""기업 그래프의 노드 연결: 공시 목록 재사용, 서로 무관한 노드의 병렬 실행."""
import asyncio
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.agents.company import nodes
from app.agents.company.graph import build_company_graph
from app.domain.models import CorpProfile, Disclosure


async def empty(state, config):
    return {}


@pytest.mark.asyncio
async def test_news_reuses_disclosures_fetched_by_disclosure_node(monkeypatch):
    found = [Disclosure(rcept_no="r", report_nm="주요사항보고서", rcept_dt=date(2026, 11, 1), pblntf_ty="B")]
    listing = AsyncMock(return_value=found)
    pipeline = AsyncMock(return_value=NS(items=[], note=None, candidates=[]))
    monkeypatch.setattr(nodes.dart_filings, "disclosures", listing)
    monkeypatch.setattr(nodes, "run_pipeline", pipeline)
    for name in ("get_filing", "collect_data", "analyze_data", "regulatory_check"):
        monkeypatch.setattr(nodes, name, empty)
    out = await build_company_graph().ainvoke({"corp": CorpProfile(corp_code="c", corp_name="테스트"), "errors": []},
                                              config={"configurable": {"dart": None, "http": None}})
    assert out["disclosures"] == found
    assert listing.await_count == 1 and pipeline.await_args.args[1] == found


@pytest.mark.asyncio
async def test_regulatory_check_runs_alongside_analyze(monkeypatch):
    started = asyncio.Event()

    async def regulatory(state, config):
        started.set()
        return {"triggers": ["financial"]}

    async def analyze(state, config):
        await asyncio.wait_for(started.wait(), 1)     # 규제 검토는 분석 결과를 쓰지 않는다
        return {}

    for name in ("get_filing", "collect_data", "get_disclosures", "get_news"):
        monkeypatch.setattr(nodes, name, empty)
    monkeypatch.setattr(nodes, "analyze_data", analyze)
    monkeypatch.setattr(nodes, "regulatory_check", regulatory)
    out = await build_company_graph().ainvoke({"corp": CorpProfile(corp_code="c", corp_name="테스트"), "errors": []})
    assert out["triggers"] == ["financial"] and "html" in out
