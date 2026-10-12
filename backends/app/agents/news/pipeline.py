"""리포트용 고정 뉴스 파이프라인(설계서 뉴스 파이프라인 1–9단계)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import numpy as np
from pydantic import BaseModel

from app.core import llm
from app.core.embeddings import cosine_matrix, embed
from app.core.search.extract import fetch_article
from app.core.search.news import SearchHit, search_news
from app.domain.dates import KST, parse_published
from app.domain.ksic import industry_name
from app.domain.models import (ASPECT_GROUPS, ASPECTS, ArticleAnalysis, ArticleJudgment, CorpProfile, Disclosure,
                               NewsItem, QueryPlan)
from app.domain.news_rank import (DEDUP_THRESHOLD, WEIGHTS, Candidate, cluster, coverage, mmr_select,
                                  representatives, score)
from app.domain.sources import domain_of, source_tier

log = logging.getLogger(__name__)
MAX_CANDIDATES = 60
CLASSIFY_BATCH = 20


@dataclass
class NewsConfig:
    coverage_retry: bool = True          # N1=False, N2=True
    dedup_threshold: float = DEDUP_THRESHOLD
    weights: dict = field(default_factory=lambda: dict(WEIGHTS))


@dataclass
class NewsResult:
    items: list[NewsItem]
    note: str | None
    queries: list[QueryPlan]
    candidates: list[Candidate]          # 평가(pooling)용: 분류까지 끝난 전체 후보
    coverage_aspects: int
    median_age_days: float | None
    researched: bool
    n_initial: int = 0                   # 커버리지 재검색 전 후보 수(candidates[:n_initial]이 N1 후보)


# ---------- 1. 질의 계획 ----------
class _Plans(BaseModel):
    plans: list[QueryPlan]


PLAN_SYSTEM = """너는 한국 상장사의 최근 뉴스를 찾는 검색 질의를 만든다.
측면 묶음마다 한국어 검색 질의를 1개씩 만든다. 질의는 2–6단어로, 기업명(또는 널리 쓰는 약칭)을 반드시 넣고
그 묶음의 두 측면을 함께 찾을 수 있는 핵심어를 넣는다. 최근 공시 제목에 사건어(수주, 증자, 소송, 인수 등)가
있으면 해당 묶음의 질의에 넣는다. 날짜·연도는 넣지 않는다.
must_include에는 기사 제목이나 요약에 나올 기업명 표기(정식명에서 (주) 뺀 이름, 약칭)를 1–3개 넣는다.
aspect에는 그 묶음의 첫 번째 측면을 그대로 쓴다."""


async def plan_queries(corp: CorpProfile, disclosures: list[Disclosure], groups: list[int], *,
                       missing: list[str] | None = None, previous: list[str] | None = None) -> list[QueryPlan]:
    name = re.sub(r"\(주\)|㈜|주식회사", "", corp.corp_name).strip()
    titles = [d.report_nm for d in disclosures[:15]]
    wanted = [ASPECT_GROUPS[g] for g in groups]
    prompt = (f"기업명: {name}\n업종: {industry_name(corp.induty_code or '') or '정보 없음'}\n"
              f"최근 90일 공시 제목: {json.dumps(titles, ensure_ascii=False)}\n\n"
              "측면 묶음(이 순서대로 하나씩):\n" + "\n".join(f"{i + 1}. {a} + {b}" for i, (a, b) in enumerate(wanted)))
    if missing:
        prompt += (f"\n\n재검색이다. 앞 검색에서 다음 측면의 기사가 없었다: {', '.join(missing)}. 이 측면을 직접 겨냥하고, "
                   f"앞 질의와 다른 핵심어를 쓴다. 앞 질의: {json.dumps(previous or [], ensure_ascii=False)}")

    def check(p: _Plans) -> str | None:
        if [q.aspect for q in p.plans] != [a for a, _ in wanted]:
            return f"묶음 {len(wanted)}개를 순서대로 하나씩 써야 한다"
        if any(not q.must_include for q in p.plans):
            return "must_include가 비어 있다"
        return None

    out = await llm.structured(_Plans, [{"role": "user", "content": prompt}], system=PLAN_SYSTEM, effort="low",
                               task="news.plan_queries", validate=check)
    for q in out.plans:  # 기업명은 항상 허용 표기에 넣는다
        if name not in q.must_include:
            q.must_include.append(name)
    return out.plans


# ---------- 7. 분류 ----------
class _Judgments(BaseModel):
    judgments: list[ArticleJudgment]


CLASSIFY_SYSTEM = """너는 기업 뉴스 기사를 분류한다. 기사마다 article_id를 그대로 쓰고 아래 기준으로 판정한다.

relevance: 0 = 다른 기업 기사이거나 대상 기업 이름만 지나가듯 나옴, 1 = 대상 기업을 다루지만 기사의 주제는 아님,
2 = 대상 기업이 기사의 주체.
aspect: 실적·재무(실적, 이익, 배당 전망), 투자·생산(설비 투자, 증설, 생산), 제품·기술(신제품, 기술, R&D),
시장·경쟁(점유율, 경쟁사, 수요), 리스크·규제(소송, 제재, 규제, 사고), 지배구조·자본(증자, 자사주, 인수합병, 경영진).
relevance가 0이면 aspect는 null.
materiality: 1 = 일상 보도(행사, 단신, 주가 시황), 2 = 실적·사업에 영향 가능, 3 = 실적·주가에 직접 영향
(대규모 수주, 실적 발표, 제재, 대형 인수).
event_type: 사건을 한두 단어로(예: 수주, 소송, 증설, 실적 발표, 목표주가 조정).
disclosure_rcept_no: 기사가 아래 공시 목록 중 하나를 다루면 그 rcept_no, 아니면 null."""


async def classify(corp_name: str, cands: list[Candidate], disclosures: list[Disclosure]) -> None:
    disc = [{"rcept_no": d.rcept_no, "title": d.report_nm, "date": d.rcept_dt.isoformat()} for d in disclosures[:30]]
    system = [{"type": "text", "text": CLASSIFY_SYSTEM, "cache_control": {"type": "ephemeral"}}]

    async def one(batch: list[Candidate]) -> None:
        items = [{"article_id": c.id, "title": c.title, "source": domain_of(c.url),
                  "date": c.published_at.date().isoformat() if c.published_at else None,
                  "text": c.text[:600]} for c in batch]
        prompt = (f"대상 기업: {corp_name}\n공시 목록: {json.dumps(disc, ensure_ascii=False)}\n\n"
                  f"기사 {len(items)}건:\n" + "\n".join(json.dumps(i, ensure_ascii=False) for i in items))
        ids = {c.id for c in batch}

        def check(j: _Judgments) -> str | None:
            got = [x.article_id for x in j.judgments]
            return None if set(got) == ids and len(got) == len(ids) else "모든 article_id를 한 번씩 판정해야 한다"

        out = await llm.structured(_Judgments, [{"role": "user", "content": prompt}], system=system, effort="low",
                                   task="news.classify", validate=check)
        by_id = {c.id: c for c in batch}
        valid_disc = {d.rcept_no for d in disclosures}
        for j in out.judgments:
            c = by_id[j.article_id]
            c.relevance, c.aspect, c.materiality = j.relevance, j.aspect, j.materiality
            c.event_type = j.event_type
            c.disclosure_rcept_no = j.disclosure_rcept_no if j.disclosure_rcept_no in valid_disc else None

    await asyncio.gather(*(one(cands[i:i + CLASSIFY_BATCH]) for i in range(0, len(cands), CLASSIFY_BATCH)))


# ---------- 9. 요약과 영향 ----------
SUMMARY_SYSTEM = """너는 증권 애널리스트를 돕는다. 기사 본문만 근거로 대상 기업에 대한 분석을 쓴다.
summary는 정확히 3문장이다. evidence에는 본문에서 한 문장을 글자 그대로 옮긴다(고치거나 줄이지 않는다).
impact_horizon: short = 1년 안의 실적에 영향, long = 그 뒤. 투자 의견(매수·매도·목표주가)은 쓰지 않는다."""

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "＂": '"', "`": "'"})


def normalize(s: str) -> str:
    return _WS.sub(" ", s.translate(_QUOTES)).strip()


def evidence_ok(evidence: str, body: str) -> bool:
    e = normalize(evidence)
    return bool(e) and e in normalize(body)


async def summarize(corp_name: str, c: Candidate) -> ArticleAnalysis | None:
    prompt = f"대상 기업: {corp_name}\n제목: {c.title}\n본문:\n{c.text[:6000]}"

    def check(a: ArticleAnalysis) -> str | None:
        if len(a.summary) != 3:
            return "summary는 정확히 3문장"
        if not evidence_ok(a.evidence, c.text):
            return "evidence가 본문의 부분 문자열이 아니다. 본문 문장을 글자 그대로 옮겨라"
        return None

    try:
        return await llm.structured(ArticleAnalysis, [{"role": "user", "content": prompt}], system=SUMMARY_SYSTEM,
                                    effort="medium", task="news.summarize", validate=check)
    except llm.StructuredOutputError as e:
        log.info("기사 분석 실패 %s: %s", c.url, e)
        return None


# ---------- 2·3·5. 검색, 날짜, 본문 ----------
def _keep(hit: SearchHit, must: list[str]) -> bool:
    hay = f"{hit.title} {hit.snippet}".replace(" ", "")
    return any(m.replace(" ", "") in hay for m in must)


async def _search_all(plans: list[QueryPlan], time_range: str, http: httpx.AsyncClient,
                      q_offset: int = 0) -> list[tuple[int, SearchHit]]:
    results = await asyncio.gather(*(search_news(p.query, time_range=time_range, http=http) for p in plans))
    out = []
    for qi, (p, hits) in enumerate(zip(plans, results)):
        out += [(q_offset + qi, h) for h in hits if _keep(h, p.must_include)]
    return out


def _interleave_cap(hits: list[tuple[int, SearchHit]], cap: int) -> list[tuple[int, SearchHit]]:
    """URL 중복을 지우고, 60건을 넘으면 질의 사이를 번갈아 가며 검색 순위가 높은 것부터 남긴다."""
    seen, uniq = set(), []
    for qi, h in sorted(hits, key=lambda x: (x[1].rank, x[0])):
        if h.url and h.url not in seen:
            seen.add(h.url)
            uniq.append((qi, h))
    return uniq[:cap]


async def _to_candidates(hits: list[tuple[int, SearchHit]], http: httpx.AsyncClient, start: int) -> list[Candidate]:
    async def one(i: int, qi: int, h: SearchHit) -> Candidate | None:
        body = h.raw_content if h.raw_content and len(h.raw_content) >= 200 else None
        published = h.published_at
        if body is None or published is None:
            got = await fetch_article(h.url, http)
            body = body or got["text"]
            page_date = parse_published(got["date"]) if got["date"] else None
            published = page_date or published   # 원문 페이지의 발행일을 우선한다
        if published is None:
            return None                          # 발행일을 끝내 못 얻은 기사는 뺀다
        return Candidate(id=f"a{start + i}", title=h.title, url=h.url, snippet=h.snippet, body=body,
                         published_at=published, query_idx=qi, rank=h.rank, source_tier=source_tier(h.url))

    got = await asyncio.gather(*(one(i, qi, h) for i, (qi, h) in enumerate(hits)))
    return [c for c in got if c is not None]


# ---------- 전체 ----------
async def run_pipeline(corp: CorpProfile, disclosures: list[Disclosure], *, http: httpx.AsyncClient,
                       config: NewsConfig | None = None, now: datetime | None = None) -> NewsResult:
    config = config or NewsConfig()
    now = now or datetime.now(KST)
    name = re.sub(r"\(주\)|㈜|주식회사", "", corp.corp_name).strip()
    disc_dates = {d.rcept_no: d.rcept_dt for d in disclosures}

    plans = await plan_queries(corp, disclosures, [0, 1, 2])
    hits = _interleave_cap(await _search_all(plans, "month", http), MAX_CANDIDATES)
    pool = await _to_candidates(hits, http, start=0)
    researched = False

    async def rank(pool: list[Candidate]):
        unclassified = {c.id for c in pool if c.relevance is None}
        # 6. 중복 묶기(전체 후보 기준으로 다시 묶는다)
        emb = await embed([c.dedup_text() for c in pool])
        sim = cosine_matrix(emb)
        labels = cluster(sim, config.dedup_threshold)
        reps = representatives(pool, labels)
        # 7. 분류(새 대표만)와 점수
        todo = [c for c in reps if c.id in unclassified]
        if todo:
            await classify(name, todo, disclosures)
        reps = [c for c in reps if c.relevance is not None and c.relevance > 0]
        for c in reps:
            score(c, now=now, disclosure_dates=disc_dates, weights=config.weights)
        reps.sort(key=lambda c: c.score, reverse=True)
        idx = {c.id: i for i, c in enumerate(pool)}
        rsim = sim[np.ix_([idx[c.id] for c in reps], [idx[c.id] for c in reps])] if reps else np.zeros((0, 0))
        picked = [reps[i] for i in mmr_select(reps, rsim)]
        return reps, picked

    if not pool:                     # 질의가 너무 좁아 0건이면 회사 이름만으로 1년 범위를 한 번 더 찾는다
        broad = [QueryPlan(aspect=ASPECTS[0], query=name, must_include=[name])]
        pool = await _to_candidates(_interleave_cap(await _search_all(broad, "year", http, q_offset=len(plans)),
                                                    MAX_CANDIDATES), http, start=0)
        plans += broad
    n_initial = len(pool)
    if not pool:                     # 그래도 없으면(소형주 등) 기사 없음
        return NewsResult(items=[], note="최근 기사를 찾지 못했다", queries=plans, candidates=[], coverage_aspects=0,
                          median_age_days=None, researched=False)
    reps, picked = await rank(pool)
    cov = coverage(picked, ASPECTS, now=now)

    # 8. 측면 수가 모자라면 빠진 측면이 속한 묶음만 time_range="year"로 1회 재검색(질의 최대 2개)
    if config.coverage_retry and cov.aspects < 3:
        need = sorted(range(3), key=lambda g: -sum(a in cov.missing_aspects for a in ASPECT_GROUPS[g]))
        need = [g for g in need if any(a in cov.missing_aspects for a in ASPECT_GROUPS[g])][:2]
        if need:
            more_plans = await plan_queries(corp, disclosures, need, missing=cov.missing_aspects,
                                            previous=[p.query for p in plans])
            more = _interleave_cap(await _search_all(more_plans, "year", http, q_offset=len(plans)), MAX_CANDIDATES)
            known = {c.url for c in pool}
            more = [(qi, h) for qi, h in more if h.url not in known]
            pool += await _to_candidates(more, http, start=len(pool))
            plans += more_plans
            researched = True
            reps, picked = await rank(pool)
            cov = coverage(picked, ASPECTS, now=now)

    # 9. 요약과 영향(실패한 기사는 다음 순위로 채운다)
    chosen_ids = {c.id for c in picked}
    backups = [c for c in reps if c.id not in chosen_ids]
    analyses = await asyncio.gather(*(summarize(name, c) for c in picked))
    final: list[tuple[Candidate, ArticleAnalysis]] = [(c, a) for c, a in zip(picked, analyses) if a is not None]
    while len(final) < min(5, len(picked)) and backups:
        c = backups.pop(0)
        a = await summarize(name, c)
        if a is not None:
            final.append((c, a))

    items = [NewsItem(id=f"n{i + 1}", title=c.title, url=c.url, source=domain_of(c.url), published_at=c.published_at,
                      aspect=c.aspect, materiality=c.materiality, score=round(c.score, 4),
                      cluster_size=c.cluster_size, analysis=a) for i, (c, a) in enumerate(final)]
    note = None
    if not cov.passed:
        med = f"{cov.median_age_days:.0f}일" if cov.median_age_days is not None else "알 수 없음"
        note = f"커버리지 미달: 덮은 측면 {cov.aspects}개, 기사 나이 중앙값 {med}"
    return NewsResult(items=items, note=note, queries=plans, candidates=pool, coverage_aspects=cov.aspects,
                      median_age_days=cov.median_age_days, researched=researched, n_initial=n_initial)
