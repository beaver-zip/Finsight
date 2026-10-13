"""법령 코퍼스 적재: 법령 Open API → data/law/corpus.json → Neo4j 인용 그래프 + Weaviate 벡터 인덱스.

scripts/ingest_law.py와 관리 API(/admin/legal/ingest)가 같이 쓴다. 벡터 컬렉션을 새로 만들면 사용자가 올린 내규도
지워지므로, 올린 문서(data/uploads/articles.json)의 조문을 다시 넣는다.
"""
from __future__ import annotations

import collections
import logging

import httpx

from app.core.law import uploads
from app.core.law.client import LawClient
from app.core.law.corpus import fetch_all, load_corpus, save
from app.core.law.graph_store import LawGraph
from app.core.law.vector_store import LawVectors, annex_chunks, article_chunks
from app.domain.citations import build_edges
from app.domain.models import Article

log = logging.getLogger(__name__)


async def ingest(*, fetch: bool = True, graph: bool = True, vectors: bool = True) -> dict:
    out: dict = {}
    if fetch:
        async with httpx.AsyncClient() as http:
            docs = await fetch_all(LawClient(http))
        save(docs)
        out["documents"] = {d.name: {"articles": len(d.articles), "annexes": len(d.annexes),
                                     "effective_date": d.effective_date.isoformat() if d.effective_date else None}
                            for d in docs}
    corpus = load_corpus()
    if graph:
        pairs = [(a.key.split(":")[0], a) for a in corpus.articles.values()]
        edges, outside = build_edges(pairs, set(corpus.articles) | set(corpus.annexes), corpus.delegations)
        g = LawGraph()
        try:
            await g.reset()
            out["graph"] = await g.load(corpus, edges, outside)
        finally:
            await g.close()
        out["outside_laws"] = len(outside)
    if vectors:
        chunks = [c for a in corpus.articles.values() for c in article_chunks(a)]
        for x in corpus.annexes.values():
            chunks += annex_chunks(x, corpus.level_of(x.key.split(":")[0]) or "")
        up = [c for d in uploads.all_docs().values() for a in d["articles"]
              for c in article_chunks(Article.model_validate(a))]
        async with LawVectors() as v:
            await v.reset()
            n = await v.add(chunks)
            n_up = await v.add(up) if up else 0
        out["vectors"] = {"chunks": n, "by_level": dict(collections.Counter(c.level for c in chunks)),
                          "upload_chunks": n_up}
    log.info("법령 적재: %s", out)
    return out


async def reset() -> dict:
    """그래프와 벡터 컬렉션을 비운다(코퍼스 저장본 data/law/corpus.json과 올린 파일은 남긴다)."""
    g = LawGraph()
    try:
        await g.reset()
    finally:
        await g.close()
    async with LawVectors() as v:
        await v.reset()
    return {"graph": "비움", "vectors": "비움"}
