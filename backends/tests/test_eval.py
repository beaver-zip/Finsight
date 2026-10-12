"""평가 러너의 순수 함수: 통계, 검색·라우팅·뉴스·리포트 수치 지표, 분할, 임계값 탐색."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from eval.common import assign_splits
from eval.stats import ci, cohen_kappa, mcnemar, paired_diff
from eval.suites.legal import ranking_metrics, sweep_threshold
from eval.suites.news import ndcg5, score_picks
from eval.suites.report import numeric_scores
from eval.suites.routing import macro_f1


def test_ci_and_paired_diff():
    c = ci([1, 0, 1, 1, None])
    assert c["mean"] == pytest.approx(0.75) and c["n"] == 4 and 0 <= c["lo"] <= c["mean"] <= c["hi"] <= 1
    d = paired_diff({"a": 0.0, "b": 1.0, "c": None}, {"a": 1.0, "b": 1.0, "c": 1.0})
    assert d["mean"] == pytest.approx(0.5) and d["n"] == 2


def test_mcnemar_and_kappa():
    a = {str(i): i < 5 for i in range(20)}
    b = {str(i): True for i in range(20)}
    r = mcnemar(a, b)
    assert (r["b01"], r["b10"]) == (15, 0) and r["p"] < 0.001
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == pytest.approx(1.0)
    assert cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == pytest.approx(0.0)


def test_ranking_metrics():
    m = ranking_metrics(["x", "a", "y", "b"], ["a", "b"])
    assert m["recall@5"] == 1.0 and m["mrr"] == pytest.approx(0.5)
    assert 0 < m["ndcg@10"] < 1
    assert ranking_metrics(["x"], []) == {}


def test_sweep_threshold_balances_refusal():
    items = [{"answerable": True, "top_score": s} for s in (0.9, 0.8, 0.6)] + \
            [{"answerable": False, "top_score": s} for s in (0.1, 0.2, 0.7)]
    best = sweep_threshold(items)
    assert best["threshold"] in (0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6)
    assert best["answer_answerable"] == 1.0 and best["refuse_unanswerable"] == pytest.approx(2 / 3, abs=1e-3)


def test_macro_f1():
    mf1, per = macro_f1([("legal", "legal"), ("company", "news"), ("news", "news"), ("chat", "chat"),
                         ("composite", "composite")])
    assert per["legal"] == 1.0 and per["company"] == 0.0 and 0 < mf1 < 1


def test_news_scores():
    now = datetime(2026, 10, 12)
    labels = {"u1": {"relevance": 2, "materiality": 3, "aspect": "실적·재무", "dup_group": "g1",
                     "published_label": "2026-10-10"},
              "u2": {"relevance": 2, "materiality": 3, "aspect": "실적·재무", "dup_group": "g1", "published_label": None},
              "u3": {"relevance": 0, "materiality": 1, "aspect": None, "dup_group": "g2", "published_label": None}}
    picks = [{"url": "u1", "published_at": (now - timedelta(days=2)).isoformat()},
             {"url": "u2", "published_at": (now - timedelta(days=4)).isoformat()},
             {"url": "zz", "published_at": None}]
    s = score_picks(picks, labels, list(labels.values()), now)
    assert s["p@5"] == pytest.approx(2 / 5) and s["dup_rate"] == pytest.approx(2 / 3)
    assert s["date_acc"] == 1.0 and s["unjudged"] == pytest.approx(1 / 3) and s["aspects"] == 1
    assert ndcg5([1, 0], [1, 1]) < 1 and ndcg5([1, 1], [1, 1]) == pytest.approx(1.0)


def test_report_numeric_scores():
    data = {"table_unit": "억원", "tables": {"annual": {"heads": ["2024", "2025"], "rows": [
        {"head": "매출액", "cells": ["900", "1,000"]}, {"head": "영업이익", "cells": ["90", "120"]},
        {"head": "순이익", "cells": ["50", "70"]}]}}}
    gold = [{"metric": "revenue", "period": "2025FY", "fs_div": "OFS", "value": "100000000000"},
            {"metric": "operating_income", "period": "2025FY", "fs_div": "OFS", "value": "10000000000"},
            {"metric": "net_income", "period": "2025FY", "fs_div": "OFS", "value": "7000000000"},
            {"metric": "total_assets", "period": "2025FY", "fs_div": "OFS", "value": "1"}]
    s = numeric_scores(data, "OFS", gold)
    assert s["num_match"] == pytest.approx(2 / 3) and s["num_coverage"] == 1.0 and s["num_error"] == pytest.approx(1 / 3)


def test_assign_splits_is_stratified_and_stable():
    rows = [{"id": f"q{i}", "t": "a" if i < 10 else "b"} for i in range(20)]
    a = assign_splits([dict(r) for r in rows], strata=lambda r: r["t"])
    b = assign_splits([dict(r) for r in rows], strata=lambda r: r["t"])
    assert [r["split"] for r in a] == [r["split"] for r in b]
    assert sum(1 for r in a if r["t"] == "a" and r["split"] == "dev") == 3
