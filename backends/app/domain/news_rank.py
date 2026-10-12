"""뉴스 중복 묶기·점수·MMR 선택·커버리지 검사(순수 로직)."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

DEDUP_THRESHOLD = 0.88
WEIGHTS = {"materiality": 0.35, "relevance": 0.20, "recency": 0.20, "volume": 0.10, "disclosure": 0.10,
           "source": 0.05}
MMR_LAMBDA = 0.7
ASPECT_CAP = 2
PICK = 5


@dataclass
class Candidate:
    id: str
    title: str
    url: str
    snippet: str
    body: str | None
    published_at: datetime | None
    query_idx: int
    rank: int
    source_tier: float = 0.5
    # 분류 후 채움
    relevance: int | None = None
    aspect: str | None = None
    materiality: int | None = None
    event_type: str | None = None
    disclosure_rcept_no: str | None = None
    cluster: int = -1
    cluster_size: int = 1
    score: float = 0.0
    parts: dict[str, float] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.body or self.snippet

    def dedup_text(self) -> str:
        return f"{self.title}\n{self.text[:300]}"


def cluster(sim: np.ndarray, threshold: float = DEDUP_THRESHOLD) -> list[int]:
    """코사인 유사도가 임계값 이상인 쌍을 잇고, 연결 요소를 사건 묶음으로 본다(union-find)."""
    n = sim.shape[0]
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= threshold:
                parent[find(i)] = find(j)
    roots: dict[int, int] = {}
    return [roots.setdefault(find(i), len(roots)) for i in range(n)]


def representatives(cands: list[Candidate], labels: list[int]) -> list[Candidate]:
    """묶음마다 본문이 가장 긴 기사를 대표로 고르고 묶음 크기를 적는다."""
    groups: dict[int, list[Candidate]] = {}
    for c, lab in zip(cands, labels):
        c.cluster = lab
        groups.setdefault(lab, []).append(c)
    reps = []
    for members in groups.values():
        rep = max(members, key=lambda c: (len(c.text), -c.rank))
        rep.cluster_size = len(members)
        reps.append(rep)
    return reps


def score(c: Candidate, *, now: datetime, disclosure_dates: dict[str, date],
          weights: dict[str, float] = WEIGHTS) -> float:
    age = max(0.0, (now - c.published_at).total_seconds() / 86_400) if c.published_at else 365.0
    disc = 0.0
    if c.disclosure_rcept_no and c.disclosure_rcept_no in disclosure_dates and c.published_at:
        if abs((c.published_at.date() - disclosure_dates[c.disclosure_rcept_no]).days) <= 3:
            disc = 1.0
    parts = {
        "materiality": ((c.materiality or 1) - 1) / 2,
        "relevance": (c.relevance or 0) / 2,
        "recency": math.exp(-age / 14),
        "volume": min(1.0, math.log(c.cluster_size) / math.log(10)),
        "disclosure": disc,
        "source": c.source_tier,
    }
    c.parts = parts
    c.score = sum(weights[k] * v for k, v in parts.items())
    return c.score


def mmr_select(cands: list[Candidate], sim: np.ndarray, *, k: int = PICK, lam: float = MMR_LAMBDA,
               aspect_cap: int = ASPECT_CAP) -> list[int]:
    """매번 lam·점수 − (1−lam)·(이미 고른 기사와의 최대 유사도)가 가장 큰 기사를 고른다.
    이미 aspect_cap건을 고른 측면의 기사는 후보에서 뺀다. sim은 cands 순서의 유사도 행렬."""
    chosen: list[int] = []
    per_aspect: dict[str, int] = {}
    remaining = set(range(len(cands)))
    while remaining and len(chosen) < k:
        best, best_val = None, -1e9
        for i in remaining:
            a = cands[i].aspect
            if a is not None and per_aspect.get(a, 0) >= aspect_cap:
                continue
            red = max((float(sim[i, j]) for j in chosen), default=0.0)
            val = lam * cands[i].score - (1 - lam) * red
            if val > best_val:
                best, best_val = i, val
        if best is None:
            break
        chosen.append(best)
        remaining.discard(best)
        a = cands[best].aspect
        if a is not None:
            per_aspect[a] = per_aspect.get(a, 0) + 1
    return chosen


@dataclass
class Coverage:
    aspects: int
    median_age_days: float | None
    passed: bool
    missing_aspects: list[str]


def coverage(picked: list[Candidate], all_aspects: tuple[str, ...], *, now: datetime) -> Coverage:
    aspects = {c.aspect for c in picked if c.aspect}
    ages = [(now - c.published_at).total_seconds() / 86_400 for c in picked if c.published_at]
    med = statistics.median(ages) if ages else None
    passed = len(aspects) >= 3 and med is not None and med <= 30
    return Coverage(len(aspects), med, passed, [a for a in all_aspects if a not in aspects])
