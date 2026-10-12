"""하위 과업 실행: 법령 서브그래프, 기업 분석 리포트 그래프, 뉴스 파이프라인.

각 함수는 SubResult(합성에 넘길 내용)와 EvidencePart(검증에 쓸 도구 출력)를 돌려준다.
기업 분석은 리포트와 같은 그래프·캐시(7일)를 쓰므로 같은 회사를 다시 물으면 비용이 들지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from app.agents.legal.answer import link_of, render_markdown
from app.agents.legal.tools import short_name
from app.agents.news.pipeline import NewsConfig, run_pipeline
from app.core.dart import filings as dart_filings
from app.core.dart.client import DartClient
from app.core.law.corpus import load_corpus
from app.domain.binding import display
from app.domain.models import (Citation, Claim, CompanyReport, CorpProfile, LegalAnswer, Metric, NewsItem,
                               SubResult)

LegalRunner = Callable[[str], Awaitable[tuple[LegalAnswer, dict[str, str]]]]
ReportBuilder = Callable[[str], Awaitable[dict]]       # corp_code → {"html", "json"(CompanyReport)}


@dataclass
class EvidencePart:
    metrics: dict[str, Metric] = field(default_factory=dict)
    news: list[NewsItem] = field(default_factory=list)
    news_text: dict[str, str] = field(default_factory=dict)
    legal: dict[str, str] = field(default_factory=dict)
    legal_answer: LegalAnswer | None = None


# ---------------- 법령
def legal_claims(ans: LegalAnswer, prefix: str = "legal") -> list[Claim]:
    """법령 답변 문단 → 검증 단위. 요약 문단은 인용이 없고, 다른 문단도 인용하지 않은 조문을 함께 말하므로
    근거는 그 문단의 인용 조문 + 답변 전체가 인용한 조문으로 둔다."""
    every = sorted({c.source for p in ans.points for c in p.citations})
    out = []
    for i, p in enumerate(ans.points, 1):
        own = list(dict.fromkeys(c.source for c in p.citations))
        out.append(Claim(id=f"{prefix}.{i}", text=p.text, legal_keys=own + [k for k in every if k not in own]))
    return out


def legal_citations(keys: list[str]) -> list[Citation]:
    corpus = load_corpus()
    out = []
    for k in keys:
        d = corpus.get(k)
        title = getattr(d, "title", "") if d is not None else ""
        out.append(Citation(kind="annex" if ":annex-" in k else "article", id=k,
                            label=f"{short_name(k)} {title}".strip(), url=link_of(k, corpus)))
    return out


async def run_legal(instruction: str, runner: LegalRunner | None) -> tuple[SubResult, EvidencePart]:
    if runner is None:
        return SubResult(agent="legal", content="", error="법령 검색 DB(Weaviate·Neo4j)가 연결되지 않았다"), EvidencePart()
    ans, texts = await runner(instruction)
    if not ans.answerable:          # 거절이면 합성이 그 조문을 근거로 쓰지 않도록 내용·인용을 비운다
        return (SubResult(agent="legal", content=f"'{instruction}'에 답할 조문을 찾지 못했다."),
                EvidencePart(legal_answer=ans))
    md = render_markdown(ans, load_corpus())
    keys = sorted({c.source for p in ans.points for c in p.citations})
    return (SubResult(agent="legal", content=md, claims=legal_claims(ans), citations=legal_citations(keys)),
            EvidencePart(legal=texts, legal_answer=ans))


# ---------------- 기업
def company_digest(rep: CompanyReport, limit_facts: int = 160) -> str:
    """합성 프롬프트에 넣을 리포트 요약: 사실표(id·이름·표시값), 본문 블록(슬롯 그대로), 기사 목록."""
    lines = [f"기업: {rep.corp.corp_name}({rep.corp.stock_code or rep.corp.corp_code}), 최근 실적 기준: {rep.latest}"]
    lines.append("사실표(id | 항목 | 값):")
    for k, m in list(rep.metrics.items())[:limit_facts]:
        lines.append(f"- {k} | {m.label}({m.period}) | {display(m)}")
    if rep.draft is not None:
        lines.append("리포트 본문(검증을 거친 문장, {{id}}는 사실표 값):")
        lines.append(f"- 제목: {rep.draft.title.text}")
        for b in rep.draft.blocks():
            lines.append(f"- [{b.id}] {b.head}: {b.text}")
    if rep.news:
        lines.append("기사:")
        for n in rep.news:
            summ = " ".join(n.analysis.summary) if n.analysis else ""
            lines.append(f"- [{n.id}] {n.title} ({n.source}) {summ}")
    return "\n".join(lines)


async def run_company(instruction: str, corp: CorpProfile, build: ReportBuilder) -> tuple[SubResult, EvidencePart]:
    out = await build(corp.corp_code)
    rep = CompanyReport.model_validate(out["json"])
    if rep.filing is None or not rep.metrics:
        return (SubResult(agent="company", content="", error=f"{corp.corp_name}의 정기보고서·재무 자료가 없다"),
                EvidencePart())
    metrics = {m.slot_id: m for m in rep.metrics.values()}
    cites = [Citation(kind="news", id=n.id, label=n.title, url=n.url) for n in rep.news]
    if rep.filing is not None:
        cites.insert(0, Citation(kind="filing", id=rep.filing.rcept_no, label=rep.filing.report_nm,
                                 url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rep.filing.rcept_no}"))
    return (SubResult(agent="company", content=company_digest(rep), citations=cites),
            EvidencePart(metrics=metrics, news=rep.news))


# ---------------- 뉴스
def news_digest(corp: CorpProfile, items: list[NewsItem], note: str | None) -> str:
    lines = [f"기업: {corp.corp_name} 최근 기사 {len(items)}건" + (f" ({note})" if note else "")]
    for n in items:
        a = n.analysis
        date = n.published_at.date().isoformat() if n.published_at else "날짜 없음"
        lines.append(f"- [{n.id}] {date} {n.title} ({n.source})" + (f" 측면: {n.aspect}" if n.aspect else ""))
        if a:
            lines.append(f"  요약: {' '.join(a.summary)}")
            lines.append(f"  영향: {a.impact_direction}·{a.impact_horizon}·{a.impact_channel}, 근거 인용: {a.evidence}")
    return "\n".join(lines)


async def run_news(instruction: str, corp: CorpProfile, *, dart: DartClient, http: httpx.AsyncClient,
                   config: NewsConfig | None = None) -> tuple[SubResult, EvidencePart]:
    disclosures = await dart_filings.disclosures(dart, corp.corp_code)
    res = await run_pipeline(corp, disclosures, http=http, config=config or NewsConfig())
    by_url = {c.url: c for c in res.candidates}
    texts = {n.id: by_url[n.url].text for n in res.items if n.url in by_url and by_url[n.url].text}
    cites = [Citation(kind="news", id=n.id, label=n.title, url=n.url) for n in res.items]
    return (SubResult(agent="news", content=news_digest(corp, res.items, res.note), citations=cites),
            EvidencePart(news=res.items, news_text=texts))


def merge_evidence(parts: list[Any]):
    from app.agents.verifier import Evidence

    ev = Evidence()
    for p in parts:
        ev.metrics.update(p.metrics)
        ev.news.update({n.id: n for n in p.news})
        ev.news_text.update(p.news_text)
        ev.legal.update(p.legal)
    return ev
