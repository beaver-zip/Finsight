"""judge 고르기: 사람 라벨과 Cohen's κ가 가장 높은 후보 모델을 고른다.

1) --make <법령 실행 결과 폴더>: 답변이 있는 문항의 (답변, 핵심 사실)마다 사람이 O/X를 적을 CSV를 만든다.
   dev 실행이면 eval/datasets/judge_human_dev.v1.csv, test 실행이면 judge_human_test.v1.csv.
2) 사람이 '사람_판정(O/X)' 칸을 채운다.
3) --score dev: 후보 3개 모델로 같은 사실을 판정해 κ를 낸다. 가장 높은 모델을 .env의 LLM_MODEL_JUDGE로 쓴다.
   κ < 0.6이면 judge.FACTS_SYSTEM 루브릭을 고쳐 다시 잰다(최대 3회, 회차는 --round로 적는다).
4) --score test: 고른 모델의 최종 κ를 test에서 한 번만 잰다. 0.6 미만이면 그 문항은 사람 라벨 결과를 싣는다.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

from eval import judge
from eval.common import DATASETS, EVAL_DIR, load_jsonl
from eval.stats import cohen_kappa


def make(run_dir: Path) -> Path:
    s = json.loads((run_dir / "summary.json").read_text())
    qa = {x["id"]: x for x in load_jsonl(next(DATASETS.glob("legal_qa.v*.jsonl")))}
    out = DATASETS / f"judge_human_{s['split']}.v1.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "fact_no", "질문", "답변", "핵심 사실", "사람_판정(O/X)"])
        for r in load_jsonl(run_dir / "items.jsonl"):
            if not r.get("answer") or not qa[r["id"]]["answerable"]:
                continue
            for i, fact in enumerate(qa[r["id"]]["key_facts"], 1):
                w.writerow([r["id"], i, qa[r["id"]]["question"], "\n".join(r["answer"]), fact, ""])
    return out


async def score(split: str, rnd: int, models: list[str]) -> dict:
    path = DATASETS / f"judge_human_{split}.v1.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    rows = [r for r in rows if r["사람_판정(O/X)"].strip().upper() in ("O", "X")]
    by_item: dict[str, list[dict]] = {}
    for r in rows:
        by_item.setdefault(r["id"], []).append(r)
    human = [r["사람_판정(O/X)"].strip().upper() == "O" for r in rows]
    result = {"split": split, "round": rnd, "n_facts": len(rows), "kappa": {}}
    for m in models:
        pred = []
        for iid, rs in by_item.items():
            v = await judge.key_facts(rs[0]["답변"], [x["핵심 사실"] for x in sorted(rs, key=lambda x: int(x["fact_no"]))],
                                      model=m)
            pred += v if v is not None else [None] * len(rs)
        pairs = [(h, p) for h, p in zip(human, pred) if p is not None]
        result["kappa"][m] = round(cohen_kappa([h for h, _ in pairs], [p for _, p in pairs]), 4) if pairs else None
    result["best"] = max(result["kappa"], key=lambda k: result["kappa"][k] or -1)
    log = EVAL_DIR / "results" / "judge_calibration.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--make", type=Path)
    ap.add_argument("--score", choices=["dev", "test"])
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--model", action="append", help="test에서는 고른 모델 하나만 준다")
    a = ap.parse_args()
    if a.make:
        print(make(a.make))
    elif a.score:
        print(json.dumps(asyncio.run(score(a.score, a.round, a.model or judge.CANDIDATES)), ensure_ascii=False,
                         indent=1))
