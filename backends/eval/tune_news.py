"""뉴스 가중치 격자 탐색과 중복 임계값 탐색(오프라인): N2 실행 결과(items.jsonl)의 분류된 후보로 점수·MMR만 다시 계산한다.

검색·분류(LLM)는 다시 하지 않는다. 임베딩은 캐시에 있다. dev 기업의 평균 nDCG@5가 가장 높은 조합을 고른다.
- 가중치: materiality {0.25, 0.35, 0.45} × relevance {0.10, 0.20, 0.30} × recency {0.10, 0.20, 0.30}, 나머지는 현재 값, 합이 1이 되게 정규화
- 중복 임계값: {0.85, 0.88, 0.91}(2026-10-12 결정)
사용: cd backends && PYTHONPATH=. python -m eval.tune_news eval/results/N2/<ts>
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np

from app.core.embeddings import cosine_matrix, embed
from app.domain.dates import KST
from app.domain.news_rank import WEIGHTS, Candidate, cluster, mmr_select, representatives, score
from eval.common import dataset, load_jsonl
from eval.suites.news import score_picks


def _cand(d: dict) -> Candidate:
    d = {"body": None, **d}
    d["published_at"] = datetime.fromisoformat(d["published_at"]) if d.get("published_at") else None
    fields = Candidate.__dataclass_fields__
    return Candidate(**{k: v for k, v in d.items() if k in fields})


async def rerank(cands: list[Candidate], disc: dict[str, date], weights: dict, th: float, now: datetime) -> list[dict]:
    if not cands:
        return []
    emb = await embed([c.dedup_text() for c in cands])
    sim = cosine_matrix(emb)
    reps = representatives(cands, cluster(sim, th))
    reps = [c for c in reps if c.relevance is not None and c.relevance > 0]
    for c in reps:
        score(c, now=now, disclosure_dates=disc, weights=weights)
    reps.sort(key=lambda c: c.score, reverse=True)
    idx = {c.id: i for i, c in enumerate(cands)}
    rsim = sim[np.ix_([idx[c.id] for c in reps], [idx[c.id] for c in reps])] if reps else np.zeros((0, 0))
    return [{"url": reps[i].url, "published_at": reps[i].published_at.isoformat() if reps[i].published_at else None}
            for i in mmr_select(reps, rsim)]


def grid() -> list[dict]:
    out = []
    for m, r, rc in itertools.product((0.25, 0.35, 0.45), (0.10, 0.20, 0.30), (0.10, 0.20, 0.30)):
        w = {**WEIGHTS, "materiality": m, "relevance": r, "recency": rc}
        s = sum(w.values())
        out.append({k: round(v / s, 4) for k, v in w.items()})
    return out


async def main(run_dir: Path) -> None:
    rows = load_jsonl(run_dir / "items.jsonl")
    labels = dataset("news_labels")
    dev = {x["corp_code"] for x in dataset("news_companies", "dev")}
    now = datetime.now(KST)
    data = []
    for r in rows:
        if r["id"] not in dev or not r.get("cands"):
            continue
        labs = {x["url"]: x for x in labels if x["corp_code"] == r["id"]}
        disc = {k: date.fromisoformat(v) for k, v in r.get("disclosures", {}).items()}
        data.append((r, labs, disc))
    results = []
    for th in (0.85, 0.88, 0.91):
        for w in grid():
            nd = []
            for r, labs, disc in data:
                picks = await rerank([_cand(c) for c in r["cands"]], disc, w, th, now)
                nd.append(score_picks(picks, labs, list(labs.values()), now)["ndcg@5"])
            results.append({"dedup_threshold": th, "weights": w, "ndcg@5": float(np.mean(nd)) if nd else None})
    results.sort(key=lambda x: -(x["ndcg@5"] or 0))
    out = {"dev_companies": len(data), "best": results[0] if results else None,
           "current": next((x for x in results if x["dedup_threshold"] == 0.88 and
                            all(abs(x["weights"][k] - v) < 1e-3 for k, v in WEIGHTS.items())), None),
           "top5": results[:5]}
    (run_dir / "tune_news.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2000])


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
