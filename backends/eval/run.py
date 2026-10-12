"""평가 실행: 설정 하나 = 실험 하나.

사용: cd backends && PYTHONPATH=. python -m eval.run --config L2 [--split dev|test|all] [--limit N]
결과: eval/results/<설정>/<타임스탬프>/items.jsonl, summary.json, summary.md(지표·95% CI·운영 지표)
설정의 effort_override({작업: effort})는 그 실행 동안만 core.llm의 effort를 바꾼다(작업별 effort 탐색).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
from statistics import median

from app.core.llm import EFFORT_OVERRIDE, ROLE_OVERRIDE
from eval.common import RESULTS, Config, dataset, init_paid_apis, load_jsonl, new_run_dir, save_jsonl, write_summary
from eval.stats import ci

SUITES = {"legal": "legal_qa", "routing": "routing", "news": "news_companies", "report": "report_companies",
          "agent": "composite_tasks", "multiturn": "multiturn"}


def ops(items: list[dict]) -> dict:
    lat = sorted(x.get("latency_s") or 0 for x in items)
    costs = [x.get("cost_usd") or 0 for x in items]
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] if lat else None
    return {"latency_p50_s": median(lat) if lat else None, "latency_p95_s": p95,
            "cost_total_usd": round(sum(costs), 4), "cost_per_item_usd": round(sum(costs) / len(costs), 5) if costs else 0,
            "errors": sum(1 for x in items if x.get("error"))}


async def main(args) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    for n in ("httpx", "httpcore", "httpx2", "neo4j", "sentence_transformers"):
        logging.getLogger(n).setLevel(logging.WARNING)
    cfg = Config.load(args.config)
    split = args.split or cfg.params.get("split", "dev")
    items = dataset(cfg.params.get("dataset", SUITES[cfg.suite]), split)
    if args.skip_done or cfg.params.get("skip_done"):
        # 앞선 실행에서 오류 없이 끝난 문항은 건너뛴다(OpenDART 요청을 며칠에 나눠 쓰는 리포트·복합 과업용)
        done = {r["id"] for d in (RESULTS / cfg.id).glob("*/items.jsonl") for r in load_jsonl(d) if not r.get("error")} \
            if (RESULTS / cfg.id).exists() else set()
        items = [x for x in items if x["id"] not in done]
    limit = args.limit or cfg.params.get("limit")
    if limit:
        items = items[:limit]
    await init_paid_apis()
    tok = EFFORT_OVERRIDE.set(cfg.params.get("effort_override") or {})
    tok2 = ROLE_OVERRIDE.set(cfg.params.get("role_override") or {})
    try:
        suite = importlib.import_module(f"eval.suites.{cfg.suite}")
        rows, meta = await suite.run(items, cfg.params)
    finally:
        EFFORT_OVERRIDE.reset(tok)
        ROLE_OVERRIDE.reset(tok2)
    names = sorted({k for r in rows for k in (r.get("scores") or {})})
    metrics = {k: ci([r["scores"].get(k) for r in rows]) for k in names}
    meta = {**(meta or {}), "ops": ops(rows), "n_items": len(rows)}
    d = new_run_dir(cfg.id)
    save_jsonl(d / "items.jsonl", rows)
    write_summary(d, cfg, split, metrics, meta=meta)
    print((d / "summary.md").read_text())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--split")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--skip-done", action="store_true", help="앞선 실행에서 끝난 문항은 건너뛴다")
    asyncio.run(main(ap.parse_args()))
