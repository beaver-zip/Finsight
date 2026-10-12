"""뉴스 실행 결과를 지금의 라벨 풀로 다시 채점한다(풀에 기사를 더한 뒤). summary를 새로 쓴다.

사용: cd backends && PYTHONPATH=. python -m eval.rescore_news eval/results/N1/<ts> eval/results/N2/<ts>
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from app.domain.dates import KST
from eval.common import Config, dataset, load_jsonl, save_jsonl, write_summary
from eval.stats import ci
from eval.suites.news import score_picks


def rescore(run: Path) -> None:
    labels = dataset("news_labels")
    s = json.loads((run / "summary.json").read_text())
    now = datetime.fromtimestamp(run.stat().st_mtime, KST)
    rows = load_jsonl(run / "items.jsonl")
    for r in rows:
        labs = {x["url"]: x for x in labels if x["corp_code"] == r["id"]}
        r["scores"] = score_picks(r["picks"], labs, list(labs.values()), now)
    save_jsonl(run / "items.jsonl", rows)
    names = sorted({k for r in rows for k in r["scores"]})
    write_summary(run, Config.load(s["config"]), s["split"], {k: ci([r["scores"].get(k) for r in rows]) for k in names},
                  meta={**s["meta"], "rescored": True})
    print((run / "summary.md").read_text())


if __name__ == "__main__":
    for d in sys.argv[1:]:
        rescore(Path(d))
