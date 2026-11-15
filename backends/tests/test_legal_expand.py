"""재검색 루프의 인용 확장: 이미 리랭크한 후보는 다시 계산하지 않고, 홉 감점은 매번 새로 한다."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.agents.legal import graph, tools

A, B = "전자금융거래법:21-2", "전자금융거래법시행령:11-4"


@pytest.mark.asyncio
async def test_expand_reuses_raw_rerank_scores_across_loops(monkeypatch):
    rerank = AsyncMock(side_effect=lambda q, texts: [0.9] * len(texts))
    monkeypatch.setattr(tools, "rerank", rerank)
    expand = AsyncMock(side_effect=[[NS(key=A, hops=2, path=[])], [NS(key=A, hops=1, path=[]), NS(key=B, hops=2, path=[])]])
    t = tools.LawTools(vectors=None, graph=NS(expand=expand))
    cfg = {"configurable": {"law_tools": t, "legal_config": graph.LegalConfig()}}
    state = {"question": "q", "seeds": []}
    first = await graph.expand(state, cfg)
    second = await graph.expand({**state, **first}, cfg)
    assert [len(call.args[1]) for call in rerank.await_args_list] == [1, 1]
    assert {h.article.key: round(h.score, 2) for h in second["context"]} == {A: 0.8, B: 0.7}
