"""법령 재적재 후 같은 실행 객체의 조회·검색·참조 확장."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.agents.legal import tools
from app.core.law import corpus, ingest, uploads


def document(text, count=1):
    articles = uploads.split_articles("법령", [text] * count)
    return NS(name="법령", key_name="법령", law_id="law", level="법률", serial=text,
              effective_date=None, promulgation_no="", articles=articles, annexes=[], delegations=[])


@pytest.mark.asyncio
async def test_existing_tools_follow_reingest_and_keep_old_data_on_fetch_error(tmp_path, monkeypatch):
    monkeypatch.setattr(corpus, "CORPUS_FILE", tmp_path / "corpus.json")
    corpus.load_corpus.cache_clear()
    corpus.save([document("old")])
    class Vectors:
        async def __aenter__(self): return self
        async def hybrid(self, *args, **kwargs):
            return [NS(chunk=NS(article_key="법령:2", heading="법령", text="new"))]
    graph = NS(expand=AsyncMock(return_value=[NS(key="법령:2", hops=1, path=[])]))
    monkeypatch.setattr(tools, "LawVectors", Vectors)
    monkeypatch.setattr(tools, "LawGraph", lambda: graph)
    monkeypatch.setattr(tools, "rerank", AsyncMock(return_value=[1.0]))
    monkeypatch.setattr(ingest, "LawClient", lambda _: None)
    monkeypatch.setattr(ingest, "fetch_all", AsyncMock(return_value=[document("new", 2)]))
    try:
        t = await tools.LawTools.open()
        assert t.corpus.get("법령:1").paragraphs[0].text == "old"
        await ingest.ingest(graph=False, vectors=False)
        assert t.corpus.get("법령:1").paragraphs[0].text == "new"
        assert t.corpus.get("법령:2").paragraphs[0].text == "new"
        assert (await t.search_law("법령"))[0].article.key == "법령:2"
        assert (await t.expand_citations("법령", ["법령:1"]))[0].article.key == "법령:2"
        monkeypatch.setattr(ingest, "fetch_all", AsyncMock(side_effect=RuntimeError("fetch failed")))
        with pytest.raises(RuntimeError, match="fetch failed"):
            await ingest.ingest(graph=False, vectors=False)
        assert t.corpus.get("법령:1").paragraphs[0].text == "new"
    finally:
        corpus.load_corpus.cache_clear()
