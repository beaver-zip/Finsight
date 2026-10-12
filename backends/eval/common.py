"""평가 공통: 데이터셋 입출력, dev/test 분할, 설정, 결과 저장.

- 평가셋은 eval/datasets/<이름>.v<N>.jsonl. 문항마다 split(dev 30% / test 70%)을 층화해 고정으로 적는다.
- 설정 eval/configs/<ID>.yaml 하나가 실험 하나다. 결과는 eval/results/<ID>/<타임스탬프>/에
  items.jsonl(문항별)과 summary.json·summary.md를 남긴다.
- LLM이 들어간 단계는 문항마다 1회만 실행한다(summary에 "1회 실행"을 적는다).
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

EVAL_DIR = Path(__file__).resolve().parent
DATASETS = EVAL_DIR / "datasets"
CONFIGS = EVAL_DIR / "configs"
RESULTS = EVAL_DIR / "results"


def load_jsonl(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = DATASETS / p
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def save_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))


def dataset(name: str, split: str | None = None) -> list[dict]:
    """가장 높은 버전의 평가셋. split을 주면 그 분할만."""
    files = sorted(DATASETS.glob(f"{name}.v*.jsonl"), key=lambda p: int(p.stem.rsplit(".v", 1)[1]))
    if not files:
        raise FileNotFoundError(f"평가셋 {name}이 없다(eval/datasets/{name}.vN.jsonl)")
    rows = load_jsonl(files[-1])
    return [r for r in rows if split in (None, "all") or r.get("split") == split]


def assign_splits(rows: list[dict], strata: Callable[[dict], str], dev_ratio: float = 0.3, seed: int = 20261012,
                  key: str = "id") -> list[dict]:
    """층마다 dev 30%, test 70%. 같은 id는 늘 같은 분할이 되도록 id 해시로 섞는다."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[strata(r)].append(r)
    for g in groups.values():
        g.sort(key=lambda r: hashlib.sha256(f"{seed}:{r[key]}".encode()).hexdigest())
        n_dev = round(len(g) * dev_ratio)
        for i, r in enumerate(g):
            r["split"] = "dev" if i < n_dev else "test"
    return rows


@dataclass
class Config:
    id: str
    suite: str
    params: dict[str, Any] = field(default_factory=dict)
    compare_to: str | None = None
    description: str = ""

    @classmethod
    def load(cls, path: Path | str) -> Config:
        p = Path(path)
        if not p.exists():
            p = CONFIGS / f"{path}.yaml"
        d = yaml.safe_load(p.read_text())
        return cls(id=d["id"], suite=d["suite"], params=d.get("params") or {}, compare_to=d.get("compare_to"),
                   description=d.get("description", ""))


def new_run_dir(config_id: str) -> Path:
    d = RESULTS / config_id / datetime.now().strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_run(config_id: str) -> Path | None:
    runs = sorted((RESULTS / config_id).glob("*/summary.json")) if (RESULTS / config_id).exists() else []
    return runs[-1].parent if runs else None


def write_summary(run_dir: Path, cfg: Config, split: str, metrics: dict[str, Any], extra_md: str = "",
                  meta: dict | None = None) -> None:
    summary = {"config": cfg.id, "suite": cfg.suite, "split": split, "params": cfg.params, "metrics": metrics,
               "meta": meta or {}, "runs": "1회 실행"}
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    lines = [f"# {cfg.id} ({cfg.suite}, {split})", "", cfg.description, "", "LLM 단계는 문항마다 1회 실행.", "",
             "| 지표 | 값 | 95% CI | n |", "| --- | --- | --- | --- |"]
    for k, v in metrics.items():
        if isinstance(v, dict) and "mean" in v:
            lines.append(f"| {k} | {v['mean']:.3f} | [{v['lo']:.3f}, {v['hi']:.3f}] | {v['n']} |")
        else:
            lines.append(f"| {k} | {v} | | |")
    if meta:
        lines += ["", "```json", json.dumps(meta, ensure_ascii=False, indent=1, default=str), "```"]
    (run_dir / "summary.md").write_text("\n".join(lines) + ("\n\n" + extra_md if extra_md else "") + "\n")


def rng(seed: int = 20261012) -> random.Random:
    return random.Random(seed)
