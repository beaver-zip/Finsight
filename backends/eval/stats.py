"""통계: 문항 단위 부트스트랩 95% 신뢰구간, 짝지은 차이의 부트스트랩, McNemar 정확 검정(양측), Cohen's κ."""
from __future__ import annotations

from typing import Sequence

import numpy as np

N_BOOT = 1000
SEED = 20261012


def ci(values: Sequence[float | None], n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """평균과 95% 부트스트랩 구간. None(문항에 해당 없음)은 뺀다."""
    x = np.array([v for v in values if v is not None], dtype=float)
    if len(x) == 0:
        return {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}
    r = np.random.default_rng(seed)
    boots = r.choice(x, size=(n_boot, len(x)), replace=True).mean(axis=1)
    return {"mean": float(x.mean()), "lo": float(np.percentile(boots, 2.5)), "hi": float(np.percentile(boots, 97.5)),
            "n": int(len(x))}


def paired_diff(a: dict[str, float | None], b: dict[str, float | None], n_boot: int = N_BOOT,
                seed: int = SEED) -> dict:
    """같은 문항 id에서 b − a의 평균 차이와 95% 구간."""
    ids = [k for k in a if k in b and a[k] is not None and b[k] is not None]
    d = np.array([b[k] - a[k] for k in ids], dtype=float)
    out = ci(d.tolist(), n_boot, seed)
    return out


def mcnemar(a: dict[str, bool | None], b: dict[str, bool | None]) -> dict:
    """이진 결과 쌍의 McNemar 정확 검정(양측). b01 = a 틀림·b 맞음, b10 = a 맞음·b 틀림."""
    from scipy.stats import binomtest

    ids = [k for k in a if k in b and a[k] is not None and b[k] is not None]
    b01 = sum(1 for k in ids if not a[k] and b[k])
    b10 = sum(1 for k in ids if a[k] and not b[k])
    n = b01 + b10
    p = 1.0 if n == 0 else float(binomtest(b01, n, 0.5, alternative="two-sided").pvalue)
    return {"b01": b01, "b10": b10, "p": p, "n": len(ids)}


def cohen_kappa(x: Sequence, y: Sequence) -> float:
    labels = sorted(set(x) | set(y), key=str)
    n = len(x)
    if n == 0:
        return float("nan")
    po = sum(1 for a, b in zip(x, y) if a == b) / n
    pe = sum((list(x).count(lab) / n) * (list(y).count(lab) / n) for lab in labels)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)
