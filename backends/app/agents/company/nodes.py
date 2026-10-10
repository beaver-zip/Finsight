"""기업 분석 그래프의 노드. 외부 클라이언트는 config["configurable"]로 주입받는다.

실패한 노드는 예외를 잡아 errors에 "노드명: 메시지"를 더하고 자기 출력 키를 비운 채 넘긴다.
"""
from __future__ import annotations

import json
import logging
import re
from functools import wraps
from typing import Any, Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from app.agents.company.state import CompanyState
from app.agents.news.pipeline import NewsConfig, run_pipeline
from app.agents.verifier import Evidence, finalize, verify
from app.core import llm
from app.core.dart import filings as dart_filings
from app.core.dart import financials as dart_fin
from app.core.market import price_vs_index
from app.domain.binding import facts_table
from app.domain.metrics import compute_metrics, industry_group
from app.domain.models import Claim, ConclusionDraft, LegalAnswer, LegalFinding

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


@guarded("filing", {"filing": None})
async def get_filing(state: CompanyState, config: RunnableConfig) -> dict:
    _progress("company.filing", "최신 정기보고서 확인")
    return {"filing": await dart_filings.latest_periodic(_deps(config)["dart"], state["corp"])}


@guarded("financials", {"snapshot": None, "history": [], "metrics": {}, "industry": "general"})
async def get_financials(state: CompanyState, config: RunnableConfig) -> dict:
    dart, corp, f = _deps(config)["dart"], state["corp"], state.get("filing")
    if f is None:
        raise RuntimeError("최근 18개월 정기보고서가 없다")
    _progress("company.financials", f"{f.report_nm} 재무제표 수집")
    snap = await dart_fin.get_snapshot(dart, corp.corp_code, f.bsns_year, f.reprt_code, acc_mt=corp.acc_mt,
                                       expected_rcept_no=f.rcept_no)
    if snap is None:
        raise RuntimeError("fnlttSinglAcntAll 응답이 없다(CFS·OFS 모두 013)")
    # 이익률 추이: 직전 3개 사업연도의 사업보고서
    last_annual = f.bsns_year - 1   # 사업보고서면 그 전년부터, 분·반기면 직전 사업연도부터
    history = []
    for y in range(last_annual, last_annual - 3, -1):
        try:
            h = await dart_fin.get_snapshot(dart, corp.corp_code, y, "11011", acc_mt=corp.acc_mt)
        except Exception as e:  # noqa: BLE001
            log.info("과거 사업보고서 %s 실패: %s", y, e)
            h = None
        if h is not None:
            history.append(h)
    ig = industry_group(snap, corp.induty_code)
    return {"snapshot": snap, "history": history, "industry": ig, "metrics": compute_metrics(snap, ig)}


@guarded("disclosures", {"disclosures": []})
async def get_disclosures(state: CompanyState, config: RunnableConfig) -> dict:
    _progress("company.disclosures", "최근 90일 주요 공시 수집")
    return {"disclosures": await dart_filings.disclosures(_deps(config)["dart"], state["corp"].corp_code)}


@guarded("price", {"price_chart": None})
async def get_price(state: CompanyState, config: RunnableConfig) -> dict:
    corp = state["corp"]
    if not corp.stock_code:
        return {"price_chart": None}
    _progress("company.price", "주가·지수 수집")
    name = re.sub(r"\(주\)|㈜", "", corp.corp_name).strip()
    return {"price_chart": await price_vs_index(corp.stock_code, name, corp_cls=corp.corp_cls)}


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


# ---------- 결론·검증·재작성 ----------
CONCLUSION_SYSTEM = """너는 한국 상장사 분석 리포트의 결론을 쓰는 애널리스트다. 아래 규칙을 지킨다.

1. 재무 숫자는 직접 쓰지 않는다. 사실표의 id를 {{id}} 슬롯으로 쓴다(예: "영업이익률은 {{op_margin.2025H1}}로"). 사실표에 없는 id는 쓰지 않는다.
   슬롯은 사실표의 display 값으로 바뀐다. 증감률 슬롯은 "34.2% 감소"처럼 방향까지 표시된다. "순이익은 전년 동기 대비 {{net_income_growth.2025H1}}했다"처럼 쓰고, 슬롯 앞뒤에 증가·감소·늘었다·줄었다를 따로 쓰지 않는다.
2. 기사에서 온 숫자나 사실은 문장 끝에 기사 ID를 [n1]처럼 붙이고, 숫자는 기사에 나온 표기 그대로 쓴다. 쓴 기사 ID는 news_ids에도 넣는다.
3. 슬롯으로 쓴 id는 metric_ids에, 인용한 조문 key는 legal_keys에 넣는다. 날짜 외의 숫자를 근거 없이 쓰지 않는다.
4. 매수·매도·보유 의견, 목표주가, 비중 확대·축소 같은 투자 권유는 쓰지 않는다.
5. 각 절은 1–3문장(주장)이다. 주장 id는 "절이름.순번"(예: "threats.2", 1부터). regulatory_risks는 법령 검토 결과가 있을 때만 쓰고, 없으면 빈 리스트.
6. stability=재무 안정성, profitability=수익성, cash_flow=현금흐름, strengths/weaknesses/opportunities/threats=SWOT, overall=종합 판단. monitoring_points는 앞으로 확인할 지표·사건 2–4개(짧은 구).
7. 근거(사실표, 기사, 공시, 조문)가 뒷받침하는 범위만 쓴다. 근거가 없는 절은 "확인할 수 있는 자료가 없다"는 한 문장으로 둔다."""


def _evidence_prompt(state: CompanyState) -> str:
    corp = state["corp"]
    facts = facts_table(state.get("metrics", {}))
    hist = []
    for h in state.get("history", []):
        ms = compute_metrics(h, state.get("industry", "general"))  # type: ignore[arg-type]
        hist += [r for r in facts_table(ms) if r["id"].split(".")[0] in ("op_margin", "net_margin", "revenue_growth")]
    news = [{"id": n.id, "title": n.title, "date": n.published_at.date().isoformat() if n.published_at else None,
             "aspect": n.aspect, "materiality": n.materiality,
             "summary": n.analysis.summary if n.analysis else [], "impact": {
                 "direction": n.analysis.impact_direction, "horizon": n.analysis.impact_horizon,
                 "channel": n.analysis.impact_channel} if n.analysis else None} for n in state.get("news", [])]
    disc = [{"date": d.rcept_dt.isoformat(), "title": d.report_nm} for d in state.get("disclosures", [])[:10]]
    legal = []
    for f in state.get("regulatory", []):
        if f.answer.answerable:
            legal.append({"question": f.question, "points": [
                {"text": p.text, "keys": sorted({c.source for c in p.citations})} for p in f.answer.points]})
    filing = state.get("filing")
    snap = state.get("snapshot")
    return json.dumps({
        "기업": corp.corp_name, "기준 보고서": filing.report_nm if filing else None,
        "재무 기준": {"CFS": "연결", "OFS": "별도"}.get(snap.fs_div) if snap else None,
        "사실표": facts, "과거 사업연도 사실표(같은 슬롯 규칙)": hist, "기사": news, "최근 주요 공시": disc,
        "법령 검토": legal}, ensure_ascii=False, indent=1)


def _check_draft(has_legal: bool):
    def check(d: ConclusionDraft) -> str | None:
        for sec in ConclusionDraft.SECTIONS:
            for i, c in enumerate(getattr(d, sec), 1):
                if c.id != f"{sec}.{i}":
                    return f"주장 id는 '{sec}.{i}' 형식이어야 한다(받은 값 {c.id})"
        if not has_legal and d.regulatory_risks:
            return "법령 검토 결과가 없으므로 regulatory_risks는 빈 리스트"
        return None
    return check


async def write_conclusion(state: CompanyState, config: RunnableConfig) -> dict:
    _progress("company.conclusion", "결론 작성")
    has_legal = any(f.answer.answerable for f in state.get("regulatory", []))
    system = [{"type": "text", "text": CONCLUSION_SYSTEM, "cache_control": {"type": "ephemeral"}}]
    try:
        draft = await llm.structured(ConclusionDraft, [{"role": "user", "content": _evidence_prompt(state)}],
                                     system=system, effort="high", task="company.conclusion",
                                     validate=_check_draft(has_legal))
    except Exception as e:  # noqa: BLE001
        log.exception("결론 실패")
        return {"draft": None, "errors": [f"conclusion: {type(e).__name__}: {e}"]}
    return {"draft": draft, "attempts": 0, "judged": {}}


def evidence_of(state: CompanyState) -> Evidence:
    return Evidence.build(state.get("metrics", {}), state.get("news", []), state.get("news_text", {}),
                          state.get("regulatory", []), state.get("legal_texts", {}))


async def verify_draft(state: CompanyState, config: RunnableConfig) -> dict:
    draft = state.get("draft")
    if draft is None:
        return {"verification": None}
    _progress("company.verify", "숫자·인용·근거 검증")
    judged = state.get("judged") or {}
    result = await verify(draft.claims(), evidence_of(state), judged=judged)
    return {"verification": result, "judged": judged}


class _Rewrites(BaseModel):
    claims: list[Claim]


REVISE_SYSTEM = CONCLUSION_SYSTEM + """

지금은 검증에 실패한 주장만 다시 쓴다. 주장마다 같은 id를 유지하고, 피드백의 사유를 고친다.
'partial'이면 근거가 지지하는 범위로 줄여 쓰고, 근거 없는 숫자는 슬롯으로 바꾸거나 지운다."""


async def revise_draft(state: CompanyState, config: RunnableConfig) -> dict:
    draft, ver = state["draft"], state["verification"]
    failed_ids = {i.claim_id for i in ver.issues}
    failed = [c for c in draft.claims() if c.id in failed_ids]
    feedback = [{"claim": c.model_dump(), "issues": [f"{i.type}: {i.detail}" for i in ver.issues if i.claim_id == c.id]}
                for c in failed]
    _progress("company.revise", f"주장 {len(failed)}개 재작성")
    prompt = _evidence_prompt(state) + "\n\n재작성할 주장과 사유:\n" + json.dumps(feedback, ensure_ascii=False, indent=1)

    def check(r: _Rewrites) -> str | None:
        return None if sorted(c.id for c in r.claims) == sorted(failed_ids) else "실패한 주장 id를 모두 한 번씩 다시 써라"

    attempts = state.get("attempts", 0) + 1
    try:
        out = await llm.structured(_Rewrites, [{"role": "user", "content": prompt}], system=REVISE_SYSTEM,
                                   effort="high", task="company.revise", validate=check)
    except Exception as e:  # noqa: BLE001
        return {"attempts": attempts, "errors": [f"revise: {type(e).__name__}: {e}"]}
    new = {c.id: c for c in out.claims}
    updated = draft.model_copy(update={
        sec: [new.get(c.id, c) for c in getattr(draft, sec)] for sec in ConclusionDraft.SECTIONS})
    return {"draft": updated, "attempts": attempts}


def after_verify(state: CompanyState) -> str:
    ver = state.get("verification")
    if ver is None or ver.passed or state.get("attempts", 0) >= MAX_REVISIONS:
        return "assemble"
    return "revise"


async def assemble(state: CompanyState, config: RunnableConfig) -> dict:
    from app.agents.company.render import margin_trend, render_report
    from app.charts import generate_chart_html

    draft, ver = state.get("draft"), state.get("verification")
    if draft is not None and ver is not None and not ver.passed:
        kept = set(c.id for c in finalize(draft.claims(), ver))
        fixed = {c.id: c for c in finalize(draft.claims(), ver)}
        draft = draft.model_copy(update={sec: [fixed[c.id] for c in getattr(draft, sec) if c.id in kept]
                                         for sec in ConclusionDraft.SECTIONS})
    _progress("company.assemble", "리포트 조립")
    charts = {}
    try:
        if state.get("price_chart"):
            charts["price"] = await generate_chart_html(state["price_chart"])
        mt = margin_trend(state)
        if mt:
            charts["margins"] = await generate_chart_html(mt)
    except Exception as e:  # noqa: BLE001
        log.warning("차트 생성 실패: %s", e)
    return {"draft": draft, "html": render_report({**state, "draft": draft}, charts)}
