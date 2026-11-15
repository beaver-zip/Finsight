"""기업 리포트의 규제 검토 노드: 법령 러너가 없을 때와 있을 때."""
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.agents.company import nodes
from app.domain.models import CorpProfile, Disclosure, LegalAnswer, NewsItem


def triggered_state():
    news = NewsItem(id="n1", title="규제 기사", url="u", source="s", published_at=None, aspect="리스크·규제")
    sanction = Disclosure(rcept_no="r", report_nm="과징금 부과", rcept_dt=date(2026, 11, 1), pblntf_ty="I")
    return {"corp": CorpProfile(corp_code="c", corp_name="테스트 금융"), "industry": "financial",
            "news": [news], "disclosures": [sanction]}


@pytest.mark.asyncio
async def test_missing_legal_runner_skips_question_llm_calls(monkeypatch):
    structured = AsyncMock(return_value=NS(question="질문"))
    monkeypatch.setattr(nodes.llm, "structured", structured)
    out = await nodes.regulatory_check(triggered_state(), {"configurable": {}})
    assert out["triggers"] == ["financial", "news", "disclosure"] and out["regulatory"] == []
    assert out["errors"] == ["regulatory: 법령 서브그래프가 연결되지 않아 규제 검토를 건너뛰었다"]
    structured.assert_not_awaited()
