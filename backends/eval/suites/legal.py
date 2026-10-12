"""법령 스위트(L1~L4).

stage:
- L1: 하이브리드 검색만(리랭크 없음), 조문별 최고 청크 점수
- L2: L1 + 리랭크(search_law)
- L3a·L3b: L2 + 인용 그래프 1·2홉 확장(리랭크 − λ·hops), 상위 context_k를 답변에 넘긴다
- L4: 법령 서브그래프 전체(질의 계획·재검색·충분성 판단)
검색 지표는 문항 원문 하나로 잰다(설계서 '지표'). 답변(answer: true)은 Haiku로 1회 실행하고 judge가 채점한다.
거절은 시드 최고 점수 < threshold. 문항별 top_score를 남겨 dev에서 임계값을 사후에 훑는다(sweep_threshold).
"""
from __future__ import annotations

import math
import time
from typing import Any

from app.agents.legal.answer import build_answer
from app.agents.legal.graph import LegalConfig, build_legal_graph
from app.agents.legal.tools import LawTools, normalize
from app.core import budget
from app.domain.models import ArticleHit
from eval import judge

STAGES = ("L1", "L2", "L3a", "L3b", "L4")


def ranking_metrics(ranked: list[str], gold: list[str]) -> dict[str, float]:
    g = set(gold)
    if not g:
        return {}
    rel = [1 if k in g else 0 for k in ranked[:10]]
    first = next((i for i, k in enumerate(ranked) if k in g), None)
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel))
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(g), 10)))
    return {"recall@5": len(g & set(ranked[:5])) / len(g), "recall@10": len(g & set(ranked[:10])) / len(g),
            "mrr": 0.0 if first is None else 1 / (first + 1), "ndcg@10": dcg / idcg if idcg else 0.0}


async def l1_rank(t: LawTools, q: str, alpha: float, limit: int, k: int = 10) -> list[ArticleHit]:
    hits = await t.vectors.hybrid(normalize(q), alpha=alpha, limit=limit)
    best: dict[str, float] = {}
    for h in hits:
        best[h.chunk.article_key] = max(best.get(h.chunk.article_key, 0.0), h.score)
    return [ArticleHit(article=t.corpus.get(key), score=s) for key, s in sorted(best.items(), key=lambda x: -x[1])[:k]
            if t.corpus.get(key) is not None]


async def run_item(t: LawTools, item: dict, p: dict[str, Any], graph=None) -> dict:
    stage = p.get("stage", "L2")
    cfg = LegalConfig(alpha=p.get("alpha", 0.5), hops=p.get("hops", 2), threshold=p.get("threshold", 0.3))
    usage = budget.start_request()
    t0 = time.perf_counter()
    out: dict[str, Any] = {"id": item["id"], "type": item["type"], "answerable": item["answerable"]}
    scores: dict[str, Any] = {}
    gold = item["gold_keys"]
    if stage == "L4":
        st = await graph.ainvoke({"question": item["question"], "errors": []},
                                 config={"configurable": {"law_tools": t, "legal_config": cfg}})
        seeds = st.get("seeds") or []
        context = st.get("context") or []
        ans = st.get("answer")
        ranked = [h.article.key for h in seeds]
    else:
        seeds = (await l1_rank(t, item["question"], cfg.alpha, cfg.limit) if stage == "L1"
                 else await t.search_law(item["question"], k=10, alpha=cfg.alpha, limit=cfg.limit))
        ranked = [h.article.key for h in seeds]
        context = seeds[:cfg.context_k]
        if stage in ("L3a", "L3b"):
            ext = await t.expand_citations(item["question"], ranked[:cfg.k], hops=1 if stage == "L3a" else 2,
                                           lam=cfg.lam, limit=cfg.expand_limit)
            pool = [h.model_copy(update={"path": [h.article.key]}) for h in seeds[:cfg.k]] + ext
            context = sorted(pool, key=lambda h: -h.score)[:cfg.context_k]
        ans = None
    top = seeds[0].score if seeds else 0.0
    out["top_score"] = top
    out["ranked"] = ranked[:10]
    out["context"] = [h.article.key for h in context]
    if item["answerable"]:
        scores.update(ranking_metrics(ranked, gold))
        if item["type"] == "multi_hop":
            scores["path_complete"] = float(set(gold) <= set(out["context"]))
    if p.get("answer"):
        if stage != "L4":
            if top < cfg.threshold:
                from app.domain.models import LegalAnswer
                ans = LegalAnswer(answerable=False, fallback=ranked[:3])
            else:
                ans, _ = await build_answer(item["question"], context, t)
        refused = not ans.answerable
        out["answer"] = [p_.text for p_ in ans.points]
        out["cited"] = sorted({c.source for p_ in ans.points for c in p_.citations})
        if item["answerable"]:
            scores["false_refusal"] = float(refused)
            if not refused:
                text = "\n".join(out["answer"])
                facts = await judge.key_facts(text, item["key_facts"])
                scores["correctness"] = None if facts is None else sum(facts) / len(facts)
                pairs = [(p_.text, " ".join(c.cited_text for c in p_.citations)) for p_ in ans.points if p_.citations]
                sup = await judge.supported(pairs)
                scores["groundedness"] = None if not sup else sum(sup) / len(sup)
                cited, g = set(out["cited"]), set(gold)
                scores["cite_precision"] = len(cited & g) / len(cited) if cited else 0.0
                scores["cite_recall"] = len(cited & g) / len(g)
        else:
            scores["refusal_correct"] = float(refused)
    out["scores"] = scores
    out["latency_s"] = round(time.perf_counter() - t0, 2)
    out["cost_usd"] = round(usage.cost_usd, 6)
    return out


def sweep_threshold(items: list[dict]) -> dict:
    """dev: 0.05~0.95를 훑어 (답 없는 문항 거절률 + 답 있는 문항 비거절률)/2가 가장 높은 임계값."""
    best = None
    for i in range(1, 20):
        th = round(i * 0.05, 2)
        un = [x for x in items if not x["answerable"]]
        an = [x for x in items if x["answerable"]]
        if not un or not an:
            return {}
        rej = sum(1 for x in un if x["top_score"] < th) / len(un)
        keep = sum(1 for x in an if x["top_score"] >= th) / len(an)
        score = (rej + keep) / 2
        if best is None or score > best["_raw"] + 1e-9:          # 동점이면 낮은 임계값을 남긴다
            best = {"threshold": th, "balanced": round(score, 4), "refuse_unanswerable": round(rej, 4),
                    "answer_answerable": round(keep, 4), "_raw": score}
    if best:
        best.pop("_raw")
    return best or {}


async def run(items: list[dict], p: dict[str, Any]) -> tuple[list[dict], dict]:
    t = await LawTools.open()
    graph = build_legal_graph() if p.get("stage") == "L4" else None
    out = []
    try:
        for it in items:
            out.append(await run_item(t, it, p, graph))
    finally:
        await t.close()
    meta = {"threshold_sweep": sweep_threshold(out), "judge": judge.judge_model()[0] + (" (임시 judge)"
                                                                                        if judge.judge_model()[1] else "")}
    return out, meta
