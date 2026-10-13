"""법령 검색 도구: 하이브리드 검색 + 리랭크(search_law), 조문·별표 조회, 인용 그래프 확장.

LangGraph 서브그래프와 채팅 에이전트가 함께 쓴다. 외부 연결(Weaviate·Neo4j)은 LawTools 하나가 가진다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.law.corpus import Corpus, load_corpus
from app.core.law.graph_store import LawGraph
from app.core.law.vector_store import LawVectors, heading_of
from app.core.reranker import rerank
from app.domain.models import Annex, Article, ArticleHit

# 약칭 → 정식 명칭(질의 정규화)
ABBREV = [
    (r"전금법\s*시행령", "전자금융거래법 시행령"), (r"전금법", "전자금융거래법"),
    (r"전자금융감독규정\s*시행세칙|감독규정\s*시행세칙|시행세칙", "전자금융감독규정시행세칙"),
    (r"(?<!전자금융)감독규정", "전자금융감독규정"), (r"CISO", "정보보호최고책임자"), (r"CEO", "최고경영자"),
]
SHORT = {"전자금융거래법": "법", "전자금융거래법시행령": "시행령", "전자금융감독규정": "감독규정",
         "전자금융감독규정시행세칙": "시행세칙"}


def normalize(q: str) -> str:
    for pat, rep in ABBREV:
        q = re.sub(pat, rep, q)
    return q


def short_name(key: str) -> str:
    """'전자금융거래법시행령:11-4' → '시행령 제11조의4', ':annex-1' → '시행령 별표 1'."""
    law, rest = key.split(":", 1)
    pre = SHORT.get(law, law)
    if rest.startswith("annex-"):
        n = rest[6:].split("-")
        return f"{pre} 별표 {n[0]}" + (f"의{n[1]}" if len(n) > 1 else "")
    n = rest.split("-")
    return f"{pre} 제{n[0]}조" + (f"의{n[1]}" if len(n) > 1 else "")


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"\s+", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def focus_text(doc: Article | Annex, query: str, max_chars: int = 1500) -> str:
    """리랭크 입력: 긴 조문은 질문과 글자 2-gram이 많이 겹치는 항부터 넣는다(리랭커는 앞 512토큰만 본다).
    정의 조항(법 제2조)처럼 긴 조문이 앞부분만 보고 낮은 점수를 받던 문제를 줄인다."""
    full = doc_text(doc)
    if len(full) <= max_chars or isinstance(doc, Annex):
        return full[:max_chars]
    q = _bigrams(query)
    paras = sorted(doc.paragraphs, key=lambda p: -len(q & _bigrams(p.text)))
    out = heading_of(doc)
    for p in paras:
        if len(out) + len(p.text) + 1 > max_chars:
            # 항이 길면(호가 많은 정의 조항) 질문과 겹치는 줄만 남긴다
            lines = sorted(p.text.split("\n"), key=lambda ln: -len(q & _bigrams(ln)))
            for ln in lines:
                if len(out) + len(ln) + 1 > max_chars:
                    break
                out += "\n" + ln
            break
        out += "\n" + p.text
    return out


def doc_text(doc: Article | Annex) -> str:
    if isinstance(doc, Annex):
        return f"[{doc.law_name}] {doc.title}\n{doc.text}"
    return heading_of(doc) + "\n" + "\n".join(p.text for p in doc.paragraphs)


@dataclass
class LawTools:
    vectors: LawVectors
    graph: LawGraph
    corpus: Corpus

    @classmethod
    async def open(cls) -> LawTools:
        v = LawVectors()
        await v.__aenter__()
        return cls(v, LawGraph(), load_corpus())

    async def close(self) -> None:
        await self.vectors.__aexit__(None, None, None)
        await self.graph.close()

    async def search_law(self, query: str, k: int = 8, levels: list[str] | None = None, alpha: float = 0.5,
                         limit: int = 20) -> list[ArticleHit]:
        """약칭 정규화 → 하이브리드(limit) → 리랭크 → 조문별 최고 청크 점수로 상위 k개 부모 조문."""
        q = normalize(query)
        hits = await self.vectors.hybrid(q, alpha=alpha, limit=limit, levels=levels)
        scores = await rerank(q, [f"{h.chunk.heading}\n{h.chunk.text}" for h in hits])
        best: dict[str, float] = {}
        for h, s in zip(hits, scores):
            best[h.chunk.article_key] = max(best.get(h.chunk.article_key, 0.0), s)
        out = []
        for key, s in sorted(best.items(), key=lambda x: -x[1])[:k]:
            doc = self.corpus.get(key)
            if doc is not None:
                out.append(ArticleHit(article=doc, score=s))
        return out

    def get_article(self, key: str) -> Article | Annex | None:
        return self.corpus.get(key)

    async def expand_citations(self, query: str, keys: list[str], hops: int = 1, lam: float = 0.1,
                               limit: int = 20) -> list[ArticleHit]:
        """시드에서 참조·위임 엣지를 hops까지 따라간 후보(가까운 순 limit개)를 질문으로 리랭크하고 점수 − λ·hops로 정렬."""
        cands = await self.graph.expand(keys, hops=hops, limit=limit)
        docs = [(c, self.corpus.get(c.key)) for c in cands]
        docs = [(c, d) for c, d in docs if d is not None]
        q = normalize(query)
        scores = await rerank(q, [focus_text(d, q) for _, d in docs])
        out = [ArticleHit(article=d, score=s - lam * c.hops, hops=c.hops, path=c.path)
               for (c, d), s in zip(docs, scores)]
        return sorted(out, key=lambda h: -h.score)
