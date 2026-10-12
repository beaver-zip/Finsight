"""법령 코퍼스 적재: 법령 Open API → data/law/corpus.json → Neo4j 인용 그래프 + Weaviate 벡터 인덱스.

사용: cd backends && PYTHONPATH=. python scripts/ingest_law.py [--skip-fetch] [--graph-only] [--vectors-only]
비용: 임베딩(bge-m3, OpenRouter) 청크 약 900개, 0.01달러 미만. 임베딩은 캐시돼 다시 돌리면 들지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import logging

import httpx

from app.core.law.client import LawClient
from app.core.law.corpus import CORPUS_FILE, fetch_all, load_corpus, save
from app.core.law.graph_store import LawGraph
from app.core.law.vector_store import LawVectors, annex_chunks, article_chunks
from app.domain.citations import build_edges

logging.basicConfig(level=logging.INFO, format="%(message)s")
for n in ("httpx", "httpcore", "neo4j"):
    logging.getLogger(n).setLevel(logging.WARNING)
log = logging.getLogger("ingest_law")


async def main(args) -> None:
    if not args.skip_fetch:
        async with httpx.AsyncClient() as http:
            save(await fetch_all(LawClient(http)))
        log.info("저장: %s", CORPUS_FILE)
    corpus = load_corpus()
    pairs = [(a.key.split(":")[0], a) for a in corpus.articles.values()]
    known = set(corpus.articles) | set(corpus.annexes)

    if not args.vectors_only:
        edges, outside = build_edges(pairs, known)
        g = LawGraph()
        try:
            await g.reset()
            counts = await g.load(corpus, edges, outside)
        finally:
            await g.close()
        log.info("그래프: %s, 코퍼스 밖 법령 %d개", counts, len(outside))

    if not args.graph_only:
        chunks = [c for a in corpus.articles.values() for c in article_chunks(a)]
        for x in corpus.annexes.values():
            chunks += annex_chunks(x, corpus.level_of(x.key.split(":")[0]) or "")
        async with LawVectors() as v:
            await v.reset()
            n = await v.add(chunks)
        log.info("벡터: 청크 %d개 %s", n, dict(collections.Counter(c.level for c in chunks)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-fetch", action="store_true", help="API를 부르지 않고 data/law/corpus.json을 쓴다")
    ap.add_argument("--graph-only", action="store_true")
    ap.add_argument("--vectors-only", action="store_true")
    asyncio.run(main(ap.parse_args()))
