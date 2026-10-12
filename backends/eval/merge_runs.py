"""여러 날에 나눠 돌린 실행을 하나로 합쳐 요약한다(같은 문항은 가장 최근 실행을 쓴다).

사용: cd backends && PYTHONPATH=. python -m eval.merge_runs R [--split test]
결과: eval/results/<설정>/merged/summary.md
"""
from __future__ import annotations

import argparse
import json

from eval.common import RESULTS, Config, load_jsonl, save_jsonl, write_summary
from eval.stats import ci


def merge(config_id: str, split: str | None) -> None:
    rows: dict[str, dict] = {}
    for d in sorted((RESULTS / config_id).glob("2*/items.jsonl")):
        s = json.loads((d.parent / "summary.json").read_text())
        if split and s["split"] != split:
            continue
        for r in load_jsonl(d):
            if not r.get("error"):
                rows[r["id"]] = r
    items = list(rows.values())
    out = RESULTS / config_id / "merged"
    save_jsonl(out / "items.jsonl", items)
    names = sorted({k for r in items for k in (r.get("scores") or {})})
    write_summary(out, Config.load(config_id), split or "all",
                  {k: ci([r["scores"].get(k) for r in items]) for k in names}, meta={"merged_items": len(items)})
    print((out / "summary.md").read_text())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--split")
    a = ap.parse_args()
    merge(a.config, a.split)
