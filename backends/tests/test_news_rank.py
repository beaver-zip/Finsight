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


async def test_embed_empty_and_pipeline_without_hits(monkeypatch):
    from app.agents.news import pipeline as P
    from app.core.embeddings import embed
    from app.domain.models import CorpProfile

    assert (await embed([])).shape == (0, 1024)

    async def plans(*a, **k):
        return []

    async def none(*a, **k):
        return []
    monkeypatch.setattr(P, "plan_queries", plans)
    monkeypatch.setattr(P, "_search_all", none)
    res = await P.run_pipeline(CorpProfile(corp_code="1", corp_name="작은회사"), [], http=None)
    assert res.items == [] and res.note == "최근 기사를 찾지 못했다"


async def test_pipeline_researches_missing_aspects_and_fills_failed_summaries(monkeypatch):
    # 첫 검색이 2개 측면만 덮으면 1년 범위로 재검색하고, 요약에 실패한 기사는 다음 순위 기사로 채운다
    from app.agents.news import pipeline as P
    from app.core.search.news import SearchHit
    from app.domain.models import ArticleAnalysis, CorpProfile, QueryPlan

    aspects = {"u0": "실적·재무", "u1": "실적·재무", "u2": "투자·생산", "u3": "투자·생산",
               "u4": "제품·기술", "u5": "리스크·규제", "u6": "시장·경쟁"}

    def hit(u, rank):
        return SearchHit(title=f"삼성전자 {u}", url=f"https://x.kr/{u}", snippet="s",
                         raw_content=f"{u} 본문 첫 문장입니다. " + "가" * 200,
                         published_at=NOW - timedelta(days=1), published_raw=None, source="x", rank=rank)

    async def plans(corp, disc, groups, **k):
        return [QueryPlan(aspect=P.ASPECT_GROUPS[g][0], query=f"q{g}", must_include=["삼성전자"]) for g in groups]

    async def search(plans, time_range, http, q_offset=0):
        urls = ["u0", "u1", "u2", "u3"] if time_range == "month" else ["u3", "u4", "u5", "u6"]   # u3은 이미 있다
        return [(q_offset, hit(u, i)) for i, u in enumerate(urls)]

    async def emb(texts):
        return np.eye(len(texts))
    classified = []

    async def classify(name, cands, disc):
        classified.append([c.id for c in cands])
        for c in cands:
            c.relevance, c.aspect, c.materiality = 2, aspects[c.url.rsplit("/", 1)[1]], 2
    fail, summarized = {"u0", "u4"}, []

    async def structured(model, messages, **k):
        u = messages[0]["content"].split("제목: 삼성전자 ", 1)[1].split("\n", 1)[0]
        summarized.append(u)
        if u in fail:
            raise P.llm.StructuredOutputError("evidence 불일치")
        return ArticleAnalysis(summary=["a.", "b.", "c."], impact_direction="neutral", impact_horizon="short",
                               impact_channel="매출", evidence=f"{u} 본문 첫 문장입니다.")
    monkeypatch.setattr(P, "plan_queries", plans)
    monkeypatch.setattr(P, "_search_all", search)
    monkeypatch.setattr(P, "embed", emb)
    monkeypatch.setattr(P, "classify", classify)
    monkeypatch.setattr(P.llm, "structured", structured)
    res = await P.run_pipeline(CorpProfile(corp_code="1", corp_name="삼성전자"), [], http=None, now=NOW)
    assert res.researched and res.n_initial == 4
    assert [c.id for c in res.candidates] == [f"a{i}" for i in range(7)]          # 재검색 후보는 a4부터
    assert classified == [["a0", "a1", "a2", "a3"], ["a4", "a5", "a6"]]            # 새 대표만 분류한다
    assert [n.id for n in res.items] == [f"n{i}" for i in range(1, 6)]
    assert not {f"https://x.kr/{u}" for u in fail} & {n.url for n in res.items} and len(summarized) == 7
