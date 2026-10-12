"""라우팅 스위트: 질문 하나로 라우터를 불러 의도를 맞히는지. macro-F1과 혼동 행렬을 meta에 남긴다."""
from __future__ import annotations

import time
from collections import Counter
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents.orchestrator.router import route
from app.core import budget

INTENTS = ["legal", "company", "news", "composite", "chat"]


def macro_f1(pairs: list[tuple[str, str]]) -> tuple[float, dict]:
    f1s = {}
    for c in INTENTS:
        tp = sum(1 for g, p in pairs if g == c and p == c)
        fp = sum(1 for g, p in pairs if g != c and p == c)
        fn = sum(1 for g, p in pairs if g == c and p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s[c] = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return sum(f1s.values()) / len(f1s), f1s


async def run(items: list[dict], p: dict[str, Any]) -> tuple[list[dict], dict]:
    out = []
    for it in items:
        usage = budget.start_request()
        t0 = time.perf_counter()
        try:
            r = await route([HumanMessage(content=it["question"])], p.get("mode", "auto"))
            pred, err = r.intent, None
        except Exception as e:  # noqa: BLE001
            pred, err = None, f"{type(e).__name__}: {e}"
        out.append({"id": it["id"], "intent": it["intent"], "pred": pred, "ambiguous": it.get("ambiguous"),
                    "error": err, "scores": {"accuracy": float(pred == it["intent"]),
                                             "accuracy_ambiguous": float(pred == it["intent"]) if it.get("ambiguous")
                                             else None},
                    "latency_s": round(time.perf_counter() - t0, 2), "cost_usd": round(usage.cost_usd, 6)})
    pairs = [(x["intent"], x["pred"] or "none") for x in out]
    mf1, per = macro_f1(pairs)
    conf = Counter(f"{g}→{p_}" for g, p_ in pairs if g != p_)
    return out, {"macro_f1": round(mf1, 4), "f1_by_intent": {k: round(v, 4) for k, v in per.items()},
                 "confusions": dict(conf.most_common())}
