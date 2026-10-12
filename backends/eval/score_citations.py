"""참조 해석기 정확도: 사람이 검수한 citation_refs.v1.csv로 정밀도·재현율을 낸다.

- 판정 O: 해석기 초안이 맞음. X: 틀림(정답_대상에 맞는 조문). 빈 칸은 세지 않는다.
- 정밀도 = 해석기가 대상을 낸 표현 중 맞은 비율, 재현율 = 대상이 있는 표현(정답 기준) 중 해석기가 맞게 낸 비율.
  초안이 "(해석 안 됨)"이면 해석기가 놓친 것이다. 정답_대상이 "(없음)"이면 참조가 아닌 표현이다.
- 둘 다 0.95 이상이면 확정, 미달이면 틀린 사례를 tests/에 넣고 고친 뒤 새 표본으로 다시 잰다(최대 2회).
사용: cd backends && PYTHONPATH=. python -m eval.score_citations
"""
from __future__ import annotations

import csv
import json

from eval.common import DATASETS, EVAL_DIR


def main() -> dict:
    rows = list(csv.DictReader((DATASETS / "citation_refs.v1.csv").open(encoding="utf-8-sig")))
    judged = [r for r in rows if r["판정(O/X)"].strip().upper() in ("O", "X")]
    produced = [r for r in judged if r["초안_대상"] != "(해석 안 됨)"]
    correct = [r for r in produced if r["판정(O/X)"].strip().upper() == "O"]
    has_target = [r for r in judged if not (r["판정(O/X)"].strip().upper() == "X"
                                            and r["정답_대상"].strip() in ("(없음)", "없음"))]
    out = {"judged": len(judged), "of": len(rows),
           "precision": round(len(correct) / len(produced), 4) if produced else None,
           "recall": round(len(correct) / len(has_target), 4) if has_target else None,
           "wrong": [{"id": r["id"], "표현": r["표현"], "초안": r["초안_대상"], "정답": r["정답_대상"]}
                     for r in judged if r["판정(O/X)"].strip().upper() == "X"]}
    out["passed"] = bool(out["precision"] and out["recall"] and out["precision"] >= 0.95 and out["recall"] >= 0.95)
    (EVAL_DIR / "results").mkdir(exist_ok=True)
    (EVAL_DIR / "results" / "citation_accuracy.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
