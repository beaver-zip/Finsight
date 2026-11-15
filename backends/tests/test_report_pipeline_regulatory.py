"""기업 리포트의 규제 검토 노드: 법령 러너가 없을 때와 있을 때."""
import asyncio
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


def barrier(n):
    """n번째 호출이 들어올 때까지 모든 호출을 붙잡는다. 차례로 await하면 시간 초과가 난다."""
    arrived, ready = [], asyncio.Event()

    async def wait(value):
        arrived.append(value)
        if len(arrived) == n:
            ready.set()
        await asyncio.wait_for(ready.wait(), 1)
        return value
    return wait


@pytest.mark.asyncio
async def test_questions_and_legal_runs_are_concurrent_and_keep_order(monkeypatch):
    ask = barrier(2)

    async def structured(model, messages, **kwargs):
        return NS(question=await ask("뉴스 질문" if "기사" in messages[0]["content"] else "공시 질문"))

    run = barrier(3)

    async def runner(q):
        return LegalAnswer(answerable=False, notes=[await run(q)]), {q: "조문"}, []

    monkeypatch.setattr(nodes.llm, "structured", structured)
    out = await nodes.regulatory_check(triggered_state(), {"configurable": {"legal_runner": runner}})
    qs = [nodes.FINANCIAL_QUESTION, "뉴스 질문", "공시 질문"]
    assert [f.question for f in out["regulatory"]] == qs
    assert [f.answer.notes for f in out["regulatory"]] == [[q] for q in qs]
    assert out["legal_texts"] == {q: "조문" for q in qs}
