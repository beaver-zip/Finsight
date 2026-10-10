from datetime import datetime, timedelta

import numpy as np

from app.domain.dates import KST
from app.domain.news_rank import Candidate, cluster, coverage, mmr_select, representatives, score
from app.domain.sources import source_tier

NOW = datetime(2026, 10, 10, tzinfo=KST)


def cand(i, aspect=None, body="", days=1, rel=2, mat=2):
    return Candidate(id=f"a{i}", title=f"t{i}", url=f"https://x/{i}", snippet="s", body=body,
                     published_at=NOW - timedelta(days=days), query_idx=0, rank=i, aspect=aspect,
                     relevance=rel, materiality=mat)


def test_cluster_threshold_and_representative():
    sim = np.array([[1, .9, .1], [.9, 1, .2], [.1, .2, 1]])
    labels = cluster(sim, 0.88)
    assert labels[0] == labels[1] != labels[2]
    cs = [cand(0, body="짧음"), cand(1, body="훨씬 더 긴 본문"), cand(2)]
    reps = representatives(cs, labels)
    assert {r.id for r in reps} == {"a1", "a2"}
    assert next(r for r in reps if r.id == "a1").cluster_size == 2


def test_mmr_aspect_cap():
    cs = [cand(i, aspect="실적·재무") for i in range(4)] + [cand(9, aspect="제품·기술", mat=1)]
    for c in cs:
        score(c, now=NOW, disclosure_dates={})
    sim = np.eye(len(cs))
    picked = mmr_select(cs, sim, k=5)
    aspects = [cs[i].aspect for i in picked]
    assert aspects.count("실적·재무") == 2 and "제품·기술" in aspects and len(picked) == 3


def test_score_disclosure_match_and_coverage():
    c = cand(0, days=2)
    c.disclosure_rcept_no = "r1"
    score(c, now=NOW, disclosure_dates={"r1": (NOW - timedelta(days=1)).date()})
    assert c.parts["disclosure"] == 1.0 and c.parts["volume"] == 0.0
    picked = [cand(0, "실적·재무"), cand(1, "제품·기술"), cand(2, "리스크·규제", days=40)]
    cov = coverage(picked, ("실적·재무", "제품·기술", "리스크·규제", "시장·경쟁"), now=NOW)
    assert cov.passed and cov.missing_aspects == ["시장·경쟁"]


def test_source_tier():
    assert source_tier("https://www.hankyung.com/article/1") == 1.0
    assert source_tier("https://biz.chosun.com/x") == 1.0
    assert source_tier("https://blog.naver.com/x") == 0.0
    assert source_tier("https://v.daum.net/v/1") == 0.5
