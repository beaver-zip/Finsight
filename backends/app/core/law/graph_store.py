"""법령 인용 그래프(Neo4j). 적재와 시드 조문에서 참조·위임 엣지를 따라가는 확장만 맡는다.

(:Law {law_id, key, name, level, effective_date, in_corpus})
(:Article {key, law_id, no, branch, title}), (:Annex {key, law_id, title})
(:Law)-[:HAS_ARTICLE]->(:Article), (:Law)-[:HAS_ANNEX]->(:Annex)
(:Article)-[:REFERS_TO {raw, from_para, to_para, to_item, source}]->(:Article|:Annex|:Law)
(:Article)-[:DELEGATES_TO {phrase, source}]->(:Article)
(:Law)-[:IMPLEMENTS]->(:Law)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.law.corpus import Corpus
from app.domain.citations import IMPLEMENTS, Edge
from app.settings import get_settings, require

log = logging.getLogger(__name__)

CONSTRAINTS = [
    "CREATE CONSTRAINT article_key IF NOT EXISTS FOR (n:Article) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT annex_key IF NOT EXISTS FOR (n:Annex) REQUIRE n.key IS UNIQUE",
    "CREATE CONSTRAINT law_key IF NOT EXISTS FOR (n:Law) REQUIRE n.key IS UNIQUE",
]


@dataclass
class Expanded:
    key: str
    hops: int
    path: list[str]


def driver() -> AsyncDriver:
    s = get_settings()
    return AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, require(s.neo4j_password, "NEO4J_PASSWORD")))


class LawGraph:
    def __init__(self, drv: AsyncDriver | None = None):
        self.drv = drv or driver()

    async def close(self) -> None:
        await self.drv.close()

    async def _run(self, q: str, **params) -> list[dict]:
        async with self.drv.session() as s:
            res = await s.run(q, **params)
            return [r.data() async for r in res]

    # ---------- 적재
    async def reset(self) -> None:
        await self._run("MATCH (n) WHERE n:Law OR n:Article OR n:Annex DETACH DELETE n")
        for c in CONSTRAINTS:
            await self._run(c)

    async def load(self, corpus: Corpus, edges: list[Edge], outside: set[str]) -> dict[str, int]:
        laws = [{k: d[k] for k in ("key_name", "law_id", "name", "level", "effective_date")} for d in corpus.docs]
        for r in laws:
            r["key"] = r.pop("key_name")
        await self._run("UNWIND $rows AS r MERGE (l:Law {key: r.key}) SET l += r, l.in_corpus = true", rows=laws)
        await self._run("UNWIND $rows AS r MERGE (l:Law {key: r}) ON CREATE SET l.name = r, l.in_corpus = false",
                        rows=sorted(outside))
        arts = [{"key": a.key, "law": a.key.split(":")[0], "law_id": a.law_id, "no": a.no, "branch": a.branch,
                 "title": a.title} for a in corpus.articles.values()]
        law_ids = {d["key_name"]: d["law_id"] for d in corpus.docs}
        await self._run("UNWIND $rows AS r MATCH (l:Law {key: r.law}) "
                        "MERGE (a:Article {key: r.key}) SET a.law_id = r.law_id, a.no = r.no, a.branch = r.branch, "
                        "a.title = r.title MERGE (l)-[:HAS_ARTICLE]->(a)", rows=arts)
        anx = [{"key": x.key, "law": x.key.split(":")[0], "law_id": law_ids.get(x.key.split(":")[0]), "title": x.title}
               for x in corpus.annexes.values()]
        await self._run("UNWIND $rows AS r MATCH (l:Law {key: r.law}) "
                        "MERGE (a:Annex {key: r.key}) SET a.law_id = r.law_id, a.title = r.title "
                        "MERGE (l)-[:HAS_ANNEX]->(a)", rows=anx)
        await self._run("UNWIND $rows AS r MATCH (a:Law {key: r[0]}), (b:Law {key: r[1]}) MERGE (a)-[:IMPLEMENTS]->(b)",
                        rows=[list(p) for p in IMPLEMENTS])
        ref = [e.__dict__ for e in edges if e.kind == "REFERS_TO"]
        await self._run("UNWIND $rows AS r MATCH (a:Article {key: r.src}) "
                        "MATCH (b) WHERE (b:Article OR b:Annex OR b:Law) AND b.key = r.dst "
                        "CREATE (a)-[:REFERS_TO {raw: r.raw, from_para: r.from_para, to_para: r.to_para, "
                        "to_item: r.to_item, source: 'regex'}]->(b)", rows=ref)
        dlg = [e.__dict__ for e in edges if e.kind == "DELEGATES_TO"]
        await self._run("UNWIND $rows AS r MATCH (a:Article {key: r.src}), (b:Article {key: r.dst}) "
                        "CREATE (a)-[:DELEGATES_TO {phrase: r.phrase, source: 'regex'}]->(b)", rows=dlg)
        cnt = await self._run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n")
        return {c["t"]: c["n"] for c in cnt}

    # ---------- 검색
    async def expand(self, seed_keys: list[str], hops: int = 1, limit: int = 50) -> list[Expanded]:
        """시드에서 참조·위임 엣지(방향 무관)를 hops까지 따라간 조문·별표. 법령 노드는 지나가지 않는다.

        seed_keys는 점수 순이다. 후보는 (홉 수, 출발 시드의 순위) 순으로 정렬한 뒤 limit개를 자른다. 홉 수로만 자르면
        같은 홉 안의 순서가 임의라, 상위 시드가 직접 참조한 조문이 잘려 나갔다(2026-10-12 평가에서 관찰)."""
        if not seed_keys:
            return []
        h = 2 if hops >= 2 else 1           # 가변 길이 상한은 파라미터로 줄 수 없어 문자열로 넣는다(1 또는 2만)
        q = (f"MATCH (s) WHERE (s:Article OR s:Annex) AND s.key IN $seeds "
             f"MATCH p = (s)-[:REFERS_TO|DELEGATES_TO*1..{h}]-(n) "
             "WHERE (n:Article OR n:Annex) AND NOT n.key IN $seeds AND none(x IN nodes(p) WHERE x:Law) "
             "WITH n, length(p) AS len, [x IN nodes(p) | x.key] AS path "
             "WITH n, min(len) AS hops, collect({len: len, path: path}) AS ps "
             "RETURN n.key AS key, hops, [q IN ps WHERE q.len = hops | q.path] AS paths LIMIT 1000")
        rows = await self._run(q, seeds=seed_keys)
        rank = {k: i for i, k in enumerate(seed_keys)}
        out = []
        for r in rows:
            best = min(r["paths"], key=lambda pth: rank.get(pth[0], len(rank)))
            out.append((r["hops"], rank.get(best[0], len(rank)), Expanded(r["key"], r["hops"], best)))
        out.sort(key=lambda x: (x[0], x[1]))
        return [e for _, _, e in out[:limit]]

    async def outside_laws(self, keys: list[str]) -> list[str]:
        """이 조문들이 참조했지만 코퍼스에 없는 법령 이름."""
        rows = await self._run("MATCH (a:Article)-[:REFERS_TO]->(l:Law {in_corpus: false}) WHERE a.key IN $keys "
                               "RETURN DISTINCT l.name AS name ORDER BY name", keys=keys)
        return [r["name"] for r in rows]
