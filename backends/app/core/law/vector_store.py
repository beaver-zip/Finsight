"""법령 벡터 인덱스(Weaviate). 검색 단위는 항(자식), 반환 단위는 조(부모)다.

항이 없는 조문은 조 전체가 한 청크이고, 별표는 제목을 머리로 붙여 1,000자 이하로 줄 단위로 자른다.
벡터는 bge-m3(heading + text)를 직접 넣고, BM25는 kagome_kr 토크나이저로 heading·text에 건다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import weaviate
from weaviate.classes.config import Configure, DataType, Property, Tokenization
from weaviate.classes.query import Filter, HybridFusion, MetadataQuery

from app.core.embeddings import embed
from app.domain.models import Annex, Article
from app.settings import get_settings

log = logging.getLogger(__name__)
COLLECTION = "LawChunk"
ANNEX_CHUNK = 1000


@dataclass
class Chunk:
    chunk_key: str
    article_key: str
    law_name: str
    level: str
    source: str
    heading: str
    text: str
    effective_date: str | None = None
    para: int | None = None


@dataclass
class ChunkHit:
    chunk: Chunk
    score: float


def heading_of(a: Article) -> str:
    no = f"제{a.no}조" + (f"의{a.branch}" if a.branch else "")
    parts = [f"[{a.law_name}]"] + [f"[{x}]" for x in (a.chapter, a.section) if x]
    return " ".join(parts) + f" {no}" + (f"({a.title})" if a.title else "")


def article_chunks(a: Article) -> list[Chunk]:
    h = heading_of(a)
    eff = a.effective_date.isoformat() if a.effective_date else None
    return [Chunk(p.key, a.key, a.law_name, a.level, a.source, h, p.text, eff, p.no) for p in a.paragraphs]


def annex_chunks(x: Annex, level: str) -> list[Chunk]:
    no = x.key.split(":annex-")[-1].replace("-", "의")
    h = f"[{x.law_name}] [별표 {no}] {x.title}"
    out, buf = [], ""
    for line in x.text.split("\n"):
        if buf and len(buf) + len(line) + 1 > ANNEX_CHUNK:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n{line}" if buf else line
    if buf:
        out.append(buf)
    return [Chunk(f"{x.key}:c{i}", x.key, x.law_name, level, "lawgo", h, t) for i, t in enumerate(out)]


def connect() -> weaviate.WeaviateAsyncClient:
    u = urlparse(get_settings().weaviate_url)
    return weaviate.use_async_with_local(host=u.hostname or "localhost", port=u.port or 8080, grpc_port=50051)


class LawVectors:
    def __init__(self, client: weaviate.WeaviateAsyncClient | None = None):
        self.client = client or connect()

    async def __aenter__(self):
        await self.client.connect()
        return self

    async def __aexit__(self, *exc):
        await self.client.close()

    async def reset(self) -> None:
        if await self.client.collections.exists(COLLECTION):
            await self.client.collections.delete(COLLECTION)
        kr = Tokenization.KAGOME_KR
        await self.client.collections.create(
            COLLECTION,
            vector_config=Configure.Vectors.self_provided(),
            properties=[
                Property(name="chunk_key", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
                Property(name="article_key", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
                Property(name="law_name", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
                Property(name="level", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
                Property(name="source", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
                Property(name="heading", data_type=DataType.TEXT, tokenization=kr),
                Property(name="text", data_type=DataType.TEXT, tokenization=kr),
                Property(name="effective_date", data_type=DataType.TEXT, skip_vectorization=True,
                         index_searchable=False),
                Property(name="para", data_type=DataType.INT, index_filterable=False),
            ])

    async def add(self, chunks: list[Chunk]) -> int:
        vecs = await embed([f"{c.heading}\n{c.text}" for c in chunks])
        col = self.client.collections.get(COLLECTION)
        from weaviate.classes.data import DataObject
        objs = [DataObject(properties={k: v for k, v in c.__dict__.items() if v is not None}, vector=v.tolist())
                for c, v in zip(chunks, vecs)]
        res = await col.data.insert_many(objs)
        if res.has_errors:
            first = next(iter(res.errors.values()))
            raise RuntimeError(f"Weaviate 적재 오류 {len(res.errors)}건: {first.message[:300]}")   # 벡터는 찍지 않는다
        return len(objs)

    async def delete_doc(self, law_name: str, source: str = "upload") -> None:
        """올린 문서 하나의 청크만 지운다(법령 코퍼스는 source가 달라 지워지지 않는다)."""
        col = self.client.collections.get(COLLECTION)
        await col.data.delete_many(where=Filter.by_property("source").equal(source)
                                   & Filter.by_property("law_name").equal(law_name))

    async def delete_source(self, source: str) -> None:
        col = self.client.collections.get(COLLECTION)
        await col.data.delete_many(where=Filter.by_property("source").equal(source))

    async def hybrid(self, query: str, *, alpha: float = 0.5, limit: int = 40,
                     levels: list[str] | None = None) -> list[ChunkHit]:
        col = self.client.collections.get(COLLECTION)
        vec = (await embed([query]))[0].tolist()
        flt = Filter.by_property("level").contains_any(levels) if levels else None
        res = await col.query.hybrid(query=query, vector=vec, alpha=alpha, limit=limit,
                                     query_properties=["heading^2", "text"], fusion_type=HybridFusion.RELATIVE_SCORE,
                                     filters=flt, return_metadata=MetadataQuery(score=True))
        out = []
        for o in res.objects:
            p = o.properties
            out.append(ChunkHit(Chunk(p["chunk_key"], p["article_key"], p["law_name"], p["level"], p["source"],
                                      p["heading"], p["text"], p.get("effective_date"), p.get("para")),
                                float(o.metadata.score or 0.0)))
        return out
