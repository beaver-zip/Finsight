"""두 실행의 쌍 비교표: 같은 문항의 지표 차이(b − a)와 95% 신뢰구간, 이진 지표는 McNemar.

사용: cd backends && PYTHONPATH=. python -m eval.compare eval/results/L2/<ts> eval/results/L3a/<ts>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from eval.common import load_jsonl
from eval.stats import mcnemar, paired_diff


def per_item(run: Path) -> dict[str, dict]:
    return {r["id"]: r.get("scores", {}) for r in load_jsonl(run / "items.jsonl")}


def compare(a_dir: Path, b_dir: Path) -> str:
    a, b = per_item(a_dir), per_item(b_dir)
    sa = json.loads((a_dir / "summary.json").read_text())
    sb = json.loads((b_dir / "summary.json").read_text())
    names = sorted({k for s in a.values() for k in s} & {k for s in b.values() for k in s})
    lines = [f"# {sa['config']} → {sb['config']} ({sb['split']})", "",
             "| 지표 | A | B | 차이(B−A) | 95% CI | McNemar p | n |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for m in names:
        va = {k: v.get(m) for k, v in a.items()}
        vb = {k: v.get(m) for k, v in b.items()}
        d = paired_diff(va, vb)
        binary = all(x in (0, 1, True, False, None) for x in list(va.values()) + list(vb.values()))
        p = mcnemar({k: None if x is None else bool(x) for k, x in va.items()},
                    {k: None if x is None else bool(x) for k, x in vb.items()})["p"] if binary else None
        ma = sa["metrics"].get(m, {}).get("mean") if isinstance(sa["metrics"].get(m), dict) else None
        mb = sb["metrics"].get(m, {}).get("mean") if isinstance(sb["metrics"].get(m), dict) else None
        fmt = lambda x: "–" if x is None else f"{x:.3f}"
        lines.append(f"| {m} | {fmt(ma)} | {fmt(mb)} | {d['mean']:+.3f} | [{d['lo']:+.3f}, {d['hi']:+.3f}] | "
                     f"{'–' if p is None else f'{p:.3f}'} | {d['n']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    md = compare(a, b)
    (b / f"compare_vs_{a.parent.name}.md").write_text(md + "\n")
    print(md)
