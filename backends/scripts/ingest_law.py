"""법령 코퍼스 적재: 법령 Open API → data/law/corpus.json → Neo4j 인용 그래프 + Weaviate 벡터 인덱스.

사용: cd backends && PYTHONPATH=. python scripts/ingest_law.py [--skip-fetch] [--graph-only] [--vectors-only]
비용: 임베딩(bge-m3, OpenRouter) 청크 약 900개, 0.01달러 미만. 임베딩은 캐시돼 다시 돌리면 들지 않는다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from app.core.law.ingest import ingest

logging.basicConfig(level=logging.INFO, format="%(message)s")
for n in ("httpx", "httpcore", "neo4j"):
    logging.getLogger(n).setLevel(logging.WARNING)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-fetch", action="store_true", help="API를 부르지 않고 data/law/corpus.json을 쓴다")
    ap.add_argument("--graph-only", action="store_true")
    ap.add_argument("--vectors-only", action="store_true")
    a = ap.parse_args()
    out = asyncio.run(ingest(fetch=not a.skip_fetch, graph=not a.vectors_only, vectors=not a.graph_only))
    print(json.dumps(out, ensure_ascii=False, indent=1))
