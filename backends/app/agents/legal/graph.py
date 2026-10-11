"""법령 리서치 서브그래프: 질문 분석 → 검색·리랭크(시드) → 인용 그래프 확장 → 충분성 판단 ⇄ 재검색(최대 2회) → 답변.

답할 근거가 없으면(시드 최고 점수 < 임계값, 또는 재검색 2회 뒤에도 부족) cited()를 부르지 않고 상위 조문 제목만 돌려준다.
"""
from __future__ import annotations

import logging
import operator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.agents.legal.answer import build_answer
from app.agents.legal.tools import LawTools, doc_text, normalize, short_name
from app.core import llm
from app.domain.models import ArticleHit, LegalAnswer

log = logging.getLogger(__name__)


@dataclass
class LegalConfig:
    k: int = 8                      # 시드 조문 수
    context_k: int = 6              # 답변에 넘기는 조문 수
    hops: int = 2
    lam: float = 0.1                # 확장 조문 감점(홉당)
    alpha: float = 0.5
    limit: int = 20                 # 하이브리드 후보 수(리랭크 대상). M1에서 40개는 검색 1회 9.6초, 20개는 7.0초
    expand_limit: int = 20          # 인용 그래프 확장 후보 수(리랭크 대상). 50개 16.1초, 20개 7.6초
    threshold: float = 0.3          # 시드 최고 리랭크 점수가 이보다 낮으면 답하지 않는다(dev로 정할 초깃값)
    max_loops: int = 2


class LegalQueryPlan(BaseModel):
    queries: list[str]              # 검색 질의 1–3개
    as_of: date | None              # 질문이 묻는 시점. 검색 필터로 쓰지 않는다


class Sufficiency(BaseModel):
    sufficient: bool
    missing: list[str]              # 빠진 정보 1–3개, 항목마다 재검색 질의 하나


class LegalState(TypedDict, total=False):
    question: str
    plan: LegalQueryPlan | None
    seeds: list[ArticleHit]
    context: list[ArticleHit]
    missing: list[str]
    loops: int
    answer: LegalAnswer | None
    texts: dict[str, str]           # 답변에 넘긴 조문 key → 본문(검증용)
    errors: Annotated[list[str], operator.add]


def _tools(config: RunnableConfig) -> LawTools:
    return config["configurable"]["law_tools"]


def _cfg(config: RunnableConfig) -> LegalConfig:
    return config["configurable"].get("legal_config") or LegalConfig()


PLAN_PROMPT = """전자금융 법령(전자금융거래법, 같은 법 시행령, 전자금융감독규정, 같은 규정 시행세칙)을 검색할 질의를 만든다.

질문: {q}

- queries: 검색 질의 1~3개. 질문의 핵심 개념을 법령 용어로 바꾼 짧은 명사구(예: "정보보호최고책임자 자격요건", "전자금융업 등록 자본금"). 질문이 여러 쟁점을 묻으면 쟁점마다 하나.
- as_of: 질문이 특정 시점의 법령을 묻으면 그 날짜, 아니면 null."""

SUFF_PROMPT = """질문에 답하는 데 아래 조문만으로 충분한지 판단한다.

질문: {q}

조문:
{ctx}

- sufficient: 질문의 모든 쟁점에 답할 조문이 있으면 true.
- missing: 부족하면 빠진 정보 1~3개를 각각 검색 질의(짧은 명사구)로. 충분하면 빈 리스트."""


async def analyze(state: LegalState, config: RunnableConfig) -> dict:
    try:
        plan = await llm.structured(LegalQueryPlan, [{"role": "user", "content": PLAN_PROMPT.format(
            q=normalize(state["question"]))}], effort="medium", task="legal.plan",
            validate=lambda p: None if 1 <= len(p.queries) <= 3 else "queries는 1~3개")
    except Exception as e:  # noqa: BLE001
        log.warning("legal.plan 실패: %s", e)
        return {"plan": LegalQueryPlan(queries=[state["question"]], as_of=None),
                "errors": [f"legal.analyze: {type(e).__name__}: {e}"]}
    return {"plan": plan, "loops": 0, "missing": []}


async def retrieve(state: LegalState, config: RunnableConfig) -> dict:
    t, c = _tools(config), _cfg(config)
    queries = state["missing"] if state.get("loops") else state["plan"].queries
    best: dict[str, ArticleHit] = {h.article.key: h for h in state.get("seeds", [])}
    for q in queries[:3]:
        for h in await t.search_law(q, k=c.k, alpha=c.alpha, limit=c.limit):
            if h.article.key not in best or h.score > best[h.article.key].score:
                best[h.article.key] = h
    seeds = sorted(best.values(), key=lambda h: -h.score)[:c.k + 4 * (state.get("loops") or 0)]
    return {"seeds": seeds}


async def expand(state: LegalState, config: RunnableConfig) -> dict:
    t, c = _tools(config), _cfg(config)
    seeds = state["seeds"]
    keys = [h.article.key for h in seeds]
    try:
        ext = await t.expand_citations(state["question"], keys, hops=c.hops, lam=c.lam, limit=c.expand_limit)
    except Exception as e:  # noqa: BLE001
        log.warning("인용 그래프 확장 실패: %s", e)
        return {"context": seeds[:c.context_k], "errors": [f"legal.expand: {type(e).__name__}: {e}"]}
    pool = [h.model_copy(update={"path": [h.article.key]}) for h in seeds] + ext
    ctx = sorted(pool, key=lambda h: -h.score)[:c.context_k]
    return {"context": ctx}


async def judge(state: LegalState, config: RunnableConfig) -> dict:
    c = _cfg(config)
    seeds = state.get("seeds") or []
    if not seeds or seeds[0].score < c.threshold:
        return {"missing": [], "loops": c.max_loops + 1}            # 근거 없음 → 거절
    ctx = "\n\n".join(f"[{short_name(h.article.key)}] {doc_text(h.article)[:1200]}" for h in state["context"])
    try:
        s = await llm.structured(Sufficiency, [{"role": "user", "content": SUFF_PROMPT.format(
            q=state["question"], ctx=ctx)}], effort="medium", task="legal.sufficiency")
    except Exception as e:  # noqa: BLE001
        return {"missing": [], "errors": [f"legal.judge: {type(e).__name__}: {e}"]}
    if s.sufficient or not s.missing:
        return {"missing": []}
    return {"missing": s.missing[:3], "loops": (state.get("loops") or 0) + 1}


def after_judge(state: LegalState, config: RunnableConfig) -> str:
    c = _cfg(config)
    loops = state.get("loops") or 0
    if loops > c.max_loops:
        return "refuse"
    return "retrieve" if state.get("missing") else "answer"


async def answer(state: LegalState, config: RunnableConfig) -> dict:
    t = _tools(config)
    try:
        ans, texts = await build_answer(state["question"], state["context"], t, plan=state.get("plan"))
    except Exception as e:  # noqa: BLE001
        log.exception("legal.answer 실패")
        return {"answer": _refusal(state), "texts": {}, "errors": [f"legal.answer: {type(e).__name__}: {e}"]}
    return {"answer": ans, "texts": texts}


def _refusal(state: LegalState) -> LegalAnswer:
    top = (state.get("seeds") or [])[:3]
    return LegalAnswer(answerable=False, fallback=[h.article.key for h in top])


async def refuse(state: LegalState, config: RunnableConfig) -> dict:
    return {"answer": _refusal(state), "texts": {}}


def build_legal_graph(checkpointer=None):
    g = StateGraph(LegalState)
    for name, fn in [("analyze", analyze), ("retrieve", retrieve), ("expand", expand), ("judge", judge),
                     ("answer", answer), ("refuse", refuse)]:
        g.add_node(name, fn)
    g.add_edge(START, "analyze")
    g.add_edge("analyze", "retrieve")
    g.add_edge("retrieve", "expand")
    g.add_edge("expand", "judge")
    g.add_conditional_edges("judge", after_judge, {"retrieve": "retrieve", "answer": "answer", "refuse": "refuse"})
    g.add_edge("answer", END)
    g.add_edge("refuse", END)
    return g.compile(checkpointer=checkpointer, name="legal")


def make_runner(graph, tools: LawTools, cfg: LegalConfig | None = None):
    """기업 리포트의 규제 노드가 쓰는 LegalRunner: 질문 → (LegalAnswer, 조문 key → 본문)."""
    async def run(question: str) -> tuple[LegalAnswer, dict[str, str]]:
        out: dict[str, Any] = await graph.ainvoke({"question": question, "errors": []},
                                                  config={"configurable": {"law_tools": tools, "legal_config": cfg}})
        return out.get("answer") or LegalAnswer(answerable=False), out.get("texts") or {}
    return run
