"""채팅 오케스트레이터: start → route → clarify(되묻기) → 하위 과업 병렬 실행(Send) → synthesize → verify ⇄ revise(최대 2회)
→ finish. chat 의도는 route 뒤 guide로 끝난다.

- 단기 메모리는 체크포인터(thread_id)가 맡고, 라우터와 합성은 최근 10개 메시지만 본다. 장기 메모리·대화 요약은 없다.
- 되묻기는 interrupt()로 하고, 같은 thread_id의 Command(resume=…)로 재개한다.
- 의존 객체(dart, http, legal_runner, report_builder)는 config["configurable"]로 받는다.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt
from pydantic import BaseModel, Field

from app.agents import verifier
from app.agents.legal.answer import render_markdown
from app.agents.orchestrator.router import Route, SubTask, recent_text, route
from app.agents.orchestrator.subagents import (EvidencePart, legal_claims, merge_evidence, run_company, run_legal,
                                               run_news)
from app.agents.orchestrator.synthesize import synthesize, to_markdown
from app.core import llm
from app.core.dart import corp as dart_corp
from app.core.law.corpus import load_corpus
from app.domain.models import Citation, Claim, CorpProfile, SubResult, VerificationResult

log = logging.getLogger(__name__)
MAX_REVISIONS = 2
MAX_ASKS = 2


class FinalAnswer(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
    markdown: str
    citations: list[Citation] = Field(default_factory=list)
    route: Route | None = None
    verification: VerificationResult | None = None


def add_or_reset(old: list | None, new: list | None) -> list:
    """None을 보내면 비운다(start가 턴마다 비운다). operator.add면 체크포인터가 이전 턴 결과를 쌓는다."""
    return [] if new is None else (old or []) + new


class OrchestratorState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    mode: str
    route: Route | None
    corp: CorpProfile | None
    asks: int
    results: Annotated[list[SubResult], add_or_reset]
    evidence: Annotated[list[EvidencePart], add_or_reset]
    claims: list[Claim]
    verification: VerificationResult | None
    judged: dict[str, Any]
    attempts: int
    answer: FinalAnswer | None
    errors: Annotated[list[str], add_or_reset]


def _deps(config: RunnableConfig) -> dict:
    return config["configurable"]


def _progress(stage: str, detail: str) -> None:
    try:
        get_stream_writer()({"type": "progress", "stage": stage, "detail": detail})
    except Exception:  # noqa: BLE001
        pass


def _question(state: OrchestratorState) -> str:
    for m in reversed(state.get("messages", [])):
        if m.type == "human":
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


# ---------------- 노드
async def start(state: OrchestratorState) -> dict:
    return {"results": None, "evidence": None, "errors": None, "route": None, "corp": None, "asks": 0,
            "claims": [], "verification": None, "judged": {}, "attempts": 0, "answer": None}


async def route_node(state: OrchestratorState) -> dict:
    _progress("chat.route", "질문 분류")
    r = await route(state["messages"], state.get("mode") or "auto")
    return {"route": r}


def _option(c: dict) -> str:
    return f"{c['corp_name']} ({c['stock_code']})" if c.get("stock_code") else c["corp_name"]


async def clarify(state: OrchestratorState, config: RunnableConfig) -> dict:
    """의도·기업을 정할 수 없으면 되묻는다. 기업은 corpCode 목록에서 찾고, 후보가 여럿이면 고르게 한다."""
    r: Route = state["route"]
    asks = state.get("asks") or 0
    if r.clarification and asks < MAX_ASKS:
        ans = interrupt({"question": r.clarification, "options": []})
        return {"messages": [HumanMessage(content=str(ans))], "asks": asks + 1, "route": None}
    if not any(t.agent in ("company", "news") for t in r.sub_tasks):
        return {}
    dart = _deps(config)["dart"]
    name = r.company
    for _ in range(MAX_ASKS + 1):
        if not name:
            name = str(interrupt({"question": "어느 기업인가요? 기업명이나 종목코드를 알려 주세요.", "options": []}))
            continue
        cands = await listed_candidates(dart, name)
        if len(cands) == 1:
            corp = await dart_corp.get_profile(dart, cands[0]["corp_code"])
            return {"corp": corp}
        if not cands:
            name = str(interrupt({"question": f"'{name}'에 해당하는 회사를 찾지 못했습니다. 기업명이나 종목코드를 다시 알려 주세요.",
                                  "options": []}))
            continue
        opts = [_option(c) for c in cands]
        ans = str(interrupt({"question": "어느 기업인가요?", "options": opts}))
        picked = next((c for c, o in zip(cands, opts) if ans.strip() in (o, c["corp_code"], c.get("stock_code"))), None)
        if picked is not None:
            return {"corp": await dart_corp.get_profile(dart, picked["corp_code"])}
        name = ans
    return {"errors": ["clarify: 대상 기업을 정하지 못했다"],
            "route": r.model_copy(update={"sub_tasks": [t for t in r.sub_tasks if t.agent == "legal"]})}


async def listed_candidates(dart, name: str, limit: int = 5) -> list[dict]:
    """채팅의 기업 후보: 이름이 같은 상장사 → 이름에 질의어가 든 상장사 → (없으면) 비상장사.
    '삼성'처럼 이름이 같은 비상장사가 있어도 상장사 후보를 먼저 보여 준다(2026-10-12 버그: '주식회사 삼성'으로 진행)."""
    rows = await dart_corp.corp_list(dart)
    q = name.strip()
    if q.isdigit() and len(q) in (6, 8):
        return await dart_corp.find_candidates(dart, q, limit)
    nq = dart_corp._norm(q)
    listed = [r for r in rows if r.get("stock_code")]
    exact = [r for r in listed if dart_corp._norm(r["corp_name"]) == nq]
    if exact:
        return exact[:limit]
    from app.core.peers import load_universe

    mcap = {u["stock_code"]: u.get("mcap_approx") or 0 for u in load_universe()}
    part = sorted([r for r in listed if nq and nq in dart_corp._norm(r["corp_name"])],
                  key=lambda r: -mcap.get(r["stock_code"], 0))       # 시가총액 큰 회사 먼저
    return part[:limit] or await dart_corp.find_candidates(dart, q, limit)


def fan_out(state: OrchestratorState):
    r = state.get("route")
    if r is None:
        return "route"
    if r.intent == "chat":
        return "guide"
    tasks = [t for t in r.sub_tasks if t.agent == "legal" or state.get("corp") is not None]
    if not tasks:
        return "finish"
    return [Send("run_task", {"task": t, "corp": state.get("corp")}) for t in tasks]


async def run_task(payload: dict, config: RunnableConfig) -> dict:
    t: SubTask = payload["task"]
    corp: CorpProfile | None = payload.get("corp")
    d = _deps(config)
    try:
        if t.agent == "legal":
            _progress("chat.legal", f"법령 검색: {t.instruction}")
            res, ev = await run_legal(t.instruction, d.get("legal_runner"))
        elif t.agent == "company":
            _progress("chat.company", f"{corp.corp_name} 리포트 확인(없으면 새로 만든다)")
            res, ev = await run_company(t.instruction, corp, d["report_builder"])
        else:
            _progress("chat.news", f"{corp.corp_name} 최근 기사 검색")
            res, ev = await run_news(t.instruction, corp, dart=d["dart"], http=d["http"])
    except Exception as e:  # noqa: BLE001
        log.exception("하위 과업 실패: %s", t.agent)
        return {"results": [SubResult(agent=t.agent, content="", error=f"{type(e).__name__}: {e}")],
                "errors": [f"run_{t.agent}: {type(e).__name__}: {e}"]}
    return {"results": [res], "evidence": [ev]}


def _legal_only(state: OrchestratorState) -> SubResult | None:
    rs = state.get("results") or []
    return rs[0] if len(rs) == 1 and rs[0].agent == "legal" and not rs[0].error else None


async def synthesize_node(state: OrchestratorState) -> dict:
    lo = _legal_only(state)
    if lo is not None:                      # 법령 답변을 그대로 쓴다(답할 수 없으면 거절문만 남기고 검증하지 않는다)
        return {"claims": lo.claims}
    _progress("chat.synthesize", "답변 작성")
    try:
        claims = await synthesize(_question(state), state.get("results") or [])
    except Exception as e:  # noqa: BLE001
        log.exception("synthesize 실패")
        return {"claims": [], "errors": [f"synthesize: {type(e).__name__}: {e}"]}
    return {"claims": claims}


async def verify_node(state: OrchestratorState) -> dict:
    claims = state.get("claims") or []
    if not claims:
        return {"verification": None}
    _progress("chat.verify", f"주장 {len(claims)}개 검증")
    ev = merge_evidence(state.get("evidence") or [])
    judged = dict(state.get("judged") or {})
    try:
        res = await verifier.verify(claims, ev, judged=judged)
    except Exception as e:  # noqa: BLE001
        log.exception("verify 실패")
        return {"verification": None, "errors": [f"verify: {type(e).__name__}: {e}"]}
    return {"verification": res, "judged": judged, "attempts": (state.get("attempts") or 0) + 1}


def after_verify(state: OrchestratorState) -> str:
    """법령 단독 답변은 재작성하지 않는다: 합성 단계에는 조문 원문이 없어 고쳐 쓰면 오히려 근거가 빠진다
    (2026-10-12 관찰). 떨어진 문단은 finish에서 빼거나 '(근거 불충분)'을 붙인다."""
    v = state.get("verification")
    if v is None or v.passed or (state.get("attempts") or 0) > MAX_REVISIONS or _legal_only(state) is not None:
        return "finish"
    return "revise"


async def revise(state: OrchestratorState) -> dict:
    _progress("chat.revise", "검증에서 지적된 문단 고치기")
    try:
        claims = await synthesize(_question(state), state.get("results") or [],
                                  issues=state["verification"].issues, prev=state.get("claims"))
    except Exception as e:  # noqa: BLE001
        return {"verification": state["verification"].model_copy(update={"passed": True}),
                "errors": [f"revise: {type(e).__name__}: {e}"]}
    return {"claims": claims}


async def finish(state: OrchestratorState) -> dict:
    claims = state.get("claims") or []
    v = state.get("verification")
    if v is not None and not v.passed:
        claims = verifier.finalize(claims, v)
    ev = merge_evidence(state.get("evidence") or [])
    cites = [c for r in state.get("results") or [] for c in r.citations]
    lo = _legal_only(state)
    parts = state.get("evidence") or []
    legal_ans = next((p.legal_answer for p in parts if p.legal_answer is not None), None)
    if lo is not None and legal_ans is not None and (not legal_ans.answerable or
                                                     all(c.id.startswith("legal.") for c in claims)):
        if legal_ans.answerable:          # 검증을 통과한(또는 표시를 붙인) 문단만 남긴다
            keep = {c.id: c.text for c in claims}
            pts = [p.model_copy(update={"text": keep[c.id]})
                   for p, c in zip(legal_ans.points, legal_claims(legal_ans)) if c.id in keep]
            legal_ans = legal_ans.model_copy(update={"points": pts})
        md = render_markdown(legal_ans, load_corpus())
    elif claims:
        md = to_markdown(claims, ev.metrics, cites)
    else:
        errs = "; ".join(r.error for r in state.get("results") or [] if r.error) or "답변을 만들지 못했습니다"
        md = f"죄송합니다. 이번 질문에는 답하지 못했습니다({errs})."
    ans = FinalAnswer(claims=claims, markdown=md, citations=cites, route=state.get("route"), verification=v)
    return {"answer": ans, "messages": [AIMessage(content=md)]}


GUIDE_SYSTEM = """너는 Finsight 챗봇이다. 할 수 있는 일은 세 가지다.
1. 전자금융 법령 리서치: 전자금융거래법·시행령·전자금융감독규정·시행세칙에서 조문을 찾아 인용과 함께 답한다.
2. 상장사 기업 분석: 실적, 밸류에이션, 동종 업종 비교, 주주환원을 공시·시세로 정리한다.
3. 기업 뉴스: 특정 기업의 최근 기사를 골라 요약한다.
인사·기능 문의에는 짧게 답하고, 범위 밖 질문에는 할 수 있는 일을 안내한다. 존댓말 2~4문장, 목록 기호 없이 쓴다."""


async def guide(state: OrchestratorState) -> dict:
    try:
        md = await llm.text([{"role": "user", "content": recent_text(state["messages"], 4)}], system=GUIDE_SYSTEM,
                            effort="low", task="chat.guide")
    except Exception as e:  # noqa: BLE001
        md = ("전자금융 법령 질문, 상장사 실적·밸류에이션 분석, 기업 최근 뉴스 요약을 도와드릴 수 있습니다. "
              "궁금한 법령 조항이나 기업명을 알려 주세요.")
        return {"answer": FinalAnswer(markdown=md, route=state.get("route")), "messages": [AIMessage(content=md)],
                "errors": [f"guide: {type(e).__name__}"]}
    return {"answer": FinalAnswer(markdown=md.strip(), route=state.get("route")),
            "messages": [AIMessage(content=md.strip())]}


# 체크포인트에 들어가는 타입(역직렬화 허용 목록). 새 타입을 상태에 넣으면 여기에 더한다
STATE_TYPES = [("app.agents.orchestrator.router", "Route"), ("app.agents.orchestrator.router", "SubTask"),
               ("app.agents.orchestrator.graph", "FinalAnswer"), ("app.agents.orchestrator.subagents", "EvidencePart")]
STATE_TYPES += [("app.domain.models", n) for n in
                ("SubResult", "LegalAnswer", "LegalPoint", "CitedSpan", "Claim", "Citation", "VerificationResult",
                 "VerificationIssue", "CorpProfile", "Metric", "SourceRef", "NewsItem", "ArticleAnalysis")]


def serializer():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer(allowed_msgpack_modules=STATE_TYPES)


def build_orchestrator(checkpointer=None):
    g = StateGraph(OrchestratorState)
    g.add_node("start", start)
    g.add_node("route", route_node)
    g.add_node("clarify", clarify)
    g.add_node("run_task", run_task)
    g.add_node("synthesize", synthesize_node)
    g.add_node("verify", verify_node)
    g.add_node("revise", revise)
    g.add_node("finish", finish)
    g.add_node("guide", guide)
    g.add_edge(START, "start")
    g.add_edge("start", "route")
    g.add_edge("route", "clarify")
    g.add_conditional_edges("clarify", fan_out, ["route", "guide", "run_task", "finish"])
    g.add_edge("run_task", "synthesize")
    g.add_edge("synthesize", "verify")
    g.add_conditional_edges("verify", after_verify, {"revise": "revise", "finish": "finish"})
    g.add_edge("revise", "verify")
    g.add_edge("finish", END)
    g.add_edge("guide", END)
    return g.compile(checkpointer=checkpointer, name="chat")
