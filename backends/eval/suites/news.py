"""뉴스 스위트(N1·N2, 가중치·중복 임계값): 기업마다 파이프라인을 돌려 선택 5건을 news_labels와 대조한다.

- 관련 = relevance ≥ 1. P@5 = 선택 중 관련 비율. nDCG@5 이득 = (관련도 ÷ 2) × 중요도, 이상적 순서는 그 기업의 라벨 풀에서.
- 측면 커버리지 = 선택의 서로 다른 측면 수, 중복률 = 선택 중 같은 dup_group이 2건 이상인 기사 비율,
  날짜 정확도 = 라벨 발행일이 있는 선택 중 수집 날짜가 같은 비율, 기사 나이 중앙값(일).
- 라벨 풀에 없는 선택(unjudged)은 관련 0으로 세고 비율을 따로 적는다(풀링 평가의 관행). 그 URL은 다음 라벨링에 쓴다.
- 가중치 격자 탐색용으로 분류까지 끝난 후보의 특징을 items에 남긴다(eval/tune_news.py가 다시 점수만 매긴다).
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict
from datetime import datetime
from statistics import median
from typing import Any

import httpx

from app.agents.news.pipeline import NewsConfig, run_pipeline
from app.core import budget
from app.core.dart import corp as dart_corp
from app.core.dart import filings as dart_filings
from app.core.dart.client import DartClient
from app.domain.dates import KST
from eval.common import dataset


def ndcg5(gains: list[float], ideal: list[float]) -> float:
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains[:5]))
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(sorted(ideal, reverse=True)[:5]))
    return dcg / idcg if idcg else 0.0


def score_picks(picks: list[dict], labels: dict[str, dict], pool: list[dict], now: datetime) -> dict:
    gain = lambda lb: (lb["relevance"] or 0) / 2 * (lb["materiality"] or 1) if lb else 0.0
    labs = [labels.get(x["url"]) for x in picks]
    groups = [lb["dup_group"] for lb in labs if lb]
    aspects = {lb["aspect"] for lb in labs if lb and lb.get("relevance") and lb.get("aspect")}
    dated = [(x, lb) for x, lb in zip(picks, labs) if lb and lb.get("published_label") and x.get("published_at")]
    ages = [(now - datetime.fromisoformat(x["published_at"])).days for x in picks if x.get("published_at")]
    return {"p@5": sum(1 for lb in labs if lb and (lb["relevance"] or 0) >= 1) / 5,
            "ndcg@5": ndcg5([gain(lb) for lb in labs], [gain(lb) for lb in pool]),
            "aspects": len(aspects),
            "dup_rate": (sum(1 for g in groups if groups.count(g) > 1) / len(picks)) if picks else None,
            "date_acc": (sum(1 for x, lb in dated if x["published_at"][:10] == lb["published_label"]) / len(dated))
            if dated else None,
            "age_median_days": median(ages) if ages else None,
            "unjudged": sum(1 for lb in labs if lb is None) / len(picks) if picks else None}


async def run(items: list[dict], p: dict[str, Any]) -> tuple[list[dict], dict]:
    labels_all = dataset("news_labels")
    cfg = NewsConfig(coverage_retry=p.get("coverage_retry", True))
    if "dedup_threshold" in p:
        cfg.dedup_threshold = p["dedup_threshold"]
    if p.get("weights"):
        cfg.weights.update(p["weights"])
    out = []
    now = datetime.now(KST)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        dart = DartClient(http, concurrency=1)
        for it in items:
            usage = budget.start_request()
            t0 = time.perf_counter()
            labels = {x["url"]: x for x in labels_all if x["corp_code"] == it["corp_code"]}
            corp = await dart_corp.get_profile(dart, it["corp_code"])
            disc = await dart_filings.disclosures(dart, it["corp_code"])
            res = await run_pipeline(corp, disc, http=http, config=cfg, now=now)
            picks = [{"url": n.url, "title": n.title,
                      "published_at": n.published_at.isoformat() if n.published_at else None} for n in res.items]
            sc = score_picks(picks, labels, list(labels.values()), now)
            out.append({"id": it["corp_code"], "company": it["name"], "picks": picks, "scores": sc,
                        "researched": res.researched, "n_candidates": len(res.candidates),
                        "unjudged_urls": [x["url"] for x in picks if x["url"] not in labels],
                        "cands": [{**{k: v for k, v in asdict(c).items() if k not in ("body",)},
                                   "published_at": c.published_at.isoformat() if c.published_at else None}
                                  for c in res.candidates],
                        "disclosures": {d.rcept_no: d.rcept_dt.isoformat() for d in disc},
                        "latency_s": round(time.perf_counter() - t0, 2), "cost_usd": round(usage.cost_usd, 6)})
    return out, {"note": "relevance≥1을 관련으로 본다. 라벨 풀 밖 선택은 관련 0(unjudged 비율 참고)"}
