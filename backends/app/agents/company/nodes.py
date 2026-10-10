"""기업 분석 그래프의 노드(리포트 v2). 외부 클라이언트는 config["configurable"]로 주입받는다.

실패한 노드는 예외를 잡아 errors에 "노드명: 메시지"를 더하고 자기 출력 키를 비운 채 넘긴다.
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import wraps
from typing import Any, Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from app.agents.company import writer
from app.agents.company.analysis import analyze, make_inputs
from app.agents.company.collect import collect
from app.agents.company.render import render_report
from app.agents.company.state import CompanyState
from app.agents.news.pipeline import NewsConfig, run_pipeline
from app.agents.verifier import Evidence, verify
from app.core import llm
from app.core.dart import filings as dart_filings
from app.domain.binding import display, number_tokens
from app.domain.dates import KST
from app.domain.models import LegalAnswer, LegalFinding, VerificationResult

log = logging.getLogger(__name__)
MAX_REVISIONS = 2
SANCTION_WORDS = ("제재", "과징금", "과태료", "영업정지", "업무정지", "인가", "등록취소")
FINANCIAL_QUESTION = "금융회사에 적용되는 전자금융거래법상 주요 의무는 무엇인가?"

LegalRunner = Callable[[str], Awaitable[tuple[LegalAnswer, dict[str, str]]]]


def _deps(config: RunnableConfig) -> dict[str, Any]:
    return config.get("configurable", {})


def _progress(stage: str, detail: str) -> None:
    try:
        get_stream_writer()({"type": "progress", "stage": stage, "detail": detail})
    except Exception:  # 스트림 밖(ainvoke)에서는 무시
        pass


def guarded(name: str, empty: dict):
    def deco(fn):
        @wraps(fn)
        async def run(state: CompanyState, config: RunnableConfig):
            try:
                return await fn(state, config)
            except Exception as e:  # noqa: BLE001
                log.exception("노드 %s 실패", name)
                return {**empty, "errors": [f"{name}: {type(e).__name__}: {e}"]}
        return run
    return deco


def _today(state: CompanyState):
    return state.get("today") or datetime.now(KST).date()


@guarded("filing", {"filing": None})
async def get_filing(state: CompanyState, config: RunnableConfig) -> dict:
    _progress("company.filing", "최신 정기보고서 확인")
    return {"filing": await dart_filings.latest_periodic(_deps(config)["dart"], state["corp"], today=_today(state))}


@guarded("collect", {"collected": None, "industry": "general"})
async def collect_data(state: CompanyState, config: RunnableConfig) -> dict:
    f = state.get("filing")
    if f is None:
        raise RuntimeError("최근 18개월 정기보고서가 없다")
    deps = _deps(config)
    _progress("company.collect", f"{f.report_nm} 기준 분기 실적·주가·동종 업종 수집")
    col = await collect(deps["dart"], deps["http"], state["corp"], f, _today(state))
    return {"collected": col, "industry": col.industry, "errors": col.errors}


@guarded("disclosures", {"disclosures": []})
async def get_disclosures(state: CompanyState, config: RunnableConfig) -> dict:
    _progress("company.disclosures", "최근 90일 주요 공시 수집")
    return {"disclosures": await dart_filings.disclosures(_deps(config)["dart"], state["corp"].corp_code,
                                                          today=_today(state))}


@guarded("news", {"news": [], "news_note": None, "news_text": {}})
async def get_news(state: CompanyState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    _progress("news.search", "측면별 질의 3개 검색 중")
    disclosures = await dart_filings.disclosures(deps["dart"], state["corp"].corp_code)  # 1시간 캐시
    res = await run_pipeline(state["corp"], disclosures, http=deps["http"],
                             config=deps.get("news_config") or NewsConfig())
    texts = {}
    by_url = {c.url: c for c in res.candidates}
    for it in res.items:
        c = by_url.get(it.url)
        if c is not None:
            texts[it.id] = c.text
    return {"news": res.items, "news_note": res.note, "news_text": texts}


@guarded("analyze", {"data": None, "metrics": {}})
async def analyze_data(state: CompanyState, config: RunnableConfig) -> dict:
    col, f = state.get("collected"), state.get("filing")
    if col is None or f is None:
        raise RuntimeError("수집 자료가 없다")
    cons = None
    pre = col.prelim
    if pre is not None and state.get("news"):
        _progress("company.analyze", f"{pre.key} 컨센서스 확인")
        op = pre.rows.get("operating_income")
        cons = await writer.extract_consensus(pre.key, f"{pre.q}분기", float(op.cur) if op and op.cur else None,
                                              state["news"], state.get("news_text", {}))
    _progress("company.analyze", "지표·차트 계산")
    inp = make_inputs(state["corp"], _today(state), f, col, consensus=cons, news=state.get("news", []),
                      disclosures=state.get("disclosures", []))
    data = analyze(inp)
    errs = [f"analyze.{n}" for n in data.notes]
    return {"data": data, "metrics": data.facts, "consensus": cons, "errors": errs}


# ---------- 기업×법령 협업 ----------
class _Question(BaseModel):
    question: str


def triggers(state: CompanyState) -> list[str]:
    out = []
    if state.get("industry") == "financial":
        out.append("financial")
    if any(n.aspect == "리스크·규제" for n in state.get("news", [])):
        out.append("news")
    if any(any(w in d.report_nm for w in SANCTION_WORDS) for d in state.get("disclosures", [])):
        out.append("disclosure")
    return out


@guarded("regulatory", {"regulatory": [], "legal_texts": {}, "triggers": []})
async def regulatory_check(state: CompanyState, config: RunnableConfig) -> dict:
    trig = triggers(state)
    runner: LegalRunner | None = _deps(config).get("legal_runner")
    if not trig:
        return {"triggers": [], "regulatory": [], "legal_texts": {}}
    questions = []
    name = state["corp"].corp_name
    if "financial" in trig:
        questions.append(FINANCIAL_QUESTION)
    if "news" in trig:
        art = max((n for n in state["news"] if n.aspect == "리스크·규제"), key=lambda n: n.score or 0)
        q = await llm.structured(_Question, [{"role": "user", "content":
            f"기업: {name}\n규제 관련 기사 제목: {art.title}\n요약: {' '.join(art.analysis.summary) if art.analysis else ''}\n\n"
            "이 기사와 관련해 전자금융거래법령에서 확인할 질문 1개를 한 문장으로 써라."}],
            effort="medium", task="company.reg_question")
        questions.append(q.question)
    if "disclosure" in trig:
        d = next(d for d in state["disclosures"] if any(w in d.report_nm for w in SANCTION_WORDS))
        q = await llm.structured(_Question, [{"role": "user", "content":
            f"기업: {name}\n제재 관련 공시 제목: {d.report_nm} ({d.rcept_dt})\n\n"
            "이 공시와 관련해 전자금융거래법령에서 확인할 질문 1개를 한 문장으로 써라."}],
            effort="medium", task="company.reg_question")
        questions.append(q.question)
    if runner is None:
        return {"triggers": trig, "regulatory": [], "legal_texts": {},
                "errors": ["regulatory: 법령 서브그래프가 연결되지 않아 규제 검토를 건너뛰었다"]}
    findings, texts = [], {}
    for q in questions[:3]:
        _progress("company.regulatory", q)
        answer, t = await runner(q)
        findings.append(LegalFinding(question=q, answer=answer))
        texts.update(t)
    return {"triggers": trig, "regulatory": findings, "legal_texts": texts}


# ---------- 본문·검증·재작성 ----------
async def write_report(state: CompanyState, config: RunnableConfig) -> dict:
    data = state.get("data")
    if data is None:
        return {"draft": None}
    _progress("company.write", "본문 작성")
    try:
        draft = await writer.write_draft(data, state.get("news", []), writer.legal_digest(state.get("regulatory")))
    except Exception as e:  # noqa: BLE001
        log.exception("본문 작성 실패")
        return {"draft": None, "errors": [f"write: {type(e).__name__}: {e}"]}
    return {"draft": draft, "attempts": 0, "judged": {}}


def evidence_of(state: CompanyState) -> Evidence:
    ev = Evidence.build(state.get("metrics", {}), state.get("news", []), state.get("news_text", {}),
                        state.get("regulatory", []), state.get("legal_texts", {}))
    data = state.get("data")
    if data is not None:
        seen = [display(m) for m in data.facts.values()] + [m.label for m in data.facts.values()]
        seen += [f"{v.get('title', '')} {v.get('subtitle', '')}" for v in data.charts.values()]
        seen += [" ".join([v.get("title", "")] + list(v.get("heads", []))) for v in data.tables.values()
                 if isinstance(v, dict)]
        ev.tokens = number_tokens(seen)
    return ev


async def verify_draft(state: CompanyState, config: RunnableConfig) -> dict:
    draft = state.get("draft")
    if draft is None:
        return {"verification": None}
    _progress("company.verify", "숫자·인용·근거 검증")
    judged = state.get("judged") or {}
    try:
        result = await verify(draft.claims(), evidence_of(state), judged=judged)
    except Exception as e:  # noqa: BLE001
        log.exception("검증 실패")
        return {"verification": None, "errors": [f"verify: {type(e).__name__}: {e}"]}
    issues = result.issues + writer.style_issues(draft, state["data"])
    return {"verification": VerificationResult(passed=not issues, issues=issues), "judged": judged}


async def revise_draft(state: CompanyState, config: RunnableConfig) -> dict:
    draft, ver = state["draft"], state["verification"]
    attempts = state.get("attempts", 0) + 1
    _progress("company.revise", f"블록 {len({i.claim_id for i in ver.issues})}개 재작성")
    try:
        new = await writer.revise(draft, ver, state["data"], state.get("news", []),
                                  writer.legal_digest(state.get("regulatory")))
    except Exception as e:  # noqa: BLE001
        return {"attempts": attempts, "errors": [f"revise: {type(e).__name__}: {e}"]}
    return {"draft": new, "attempts": attempts}


def after_verify(state: CompanyState) -> str:
    ver = state.get("verification")
    if ver is None or ver.passed or state.get("attempts", 0) >= MAX_REVISIONS:
        return "assemble"
    return "revise"


async def assemble(state: CompanyState, config: RunnableConfig) -> dict:
    data = state.get("data")
    if data is None:
        return {"html": "", "errors": ["assemble: 분석 자료가 없어 리포트를 만들지 못했다"]}
    draft, ver = state.get("draft"), state.get("verification")
    errs = []
    if draft is not None:
        hero = data.charts.get("hero", {})
        draft, dropped = writer.finalize(draft, ver, hero.get("title") or data.corp["name"])
        if dropped:
            errs.append(f"verify: 재작성 뒤에도 검증을 통과하지 못해 뺀 블록·제목 {', '.join(dropped)}")
    _progress("company.assemble", "리포트 조립")
    col = state.get("collected")
    fs = None
    if col is not None and col.quarters:
        fs = col.quarters[-1].fs_div
    html = render_report(data, draft, fs_div=fs)
    return {"draft": draft, "html": html, "errors": errs}
