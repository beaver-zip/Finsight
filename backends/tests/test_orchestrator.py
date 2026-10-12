"""채팅 오케스트레이터 흐름(LLM·DB 없이): 법령 단독, 기업 되묻기 → 재개, 안내, 검증 실패 → 재작성 → 표시."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.orchestrator import graph as G
from app.agents.orchestrator.router import Route, SubTask, apply_mode
from app.agents.orchestrator.subagents import EvidencePart
from app.domain.models import (CitedSpan, Claim, CorpProfile, LegalAnswer, LegalPoint, SubResult, VerificationIssue,
                               VerificationResult)

LEGAL_ANS = LegalAnswer(answerable=True, points=[
    LegalPoint(text="정보보호최고책임자의 자격은 시행령 별표 1에 정해져 있습니다.",
               citations=[CitedSpan(source="전자금융거래법시행령:11-4", paragraph_index=4, cited_text="…별표 1과 같다.")]),
    LegalPoint(text="학위와 경력을 함께 봅니다.",
               citations=[CitedSpan(source="전자금융거래법시행령:annex-1", paragraph_index=None, cited_text="…")])])


async def fake_runner(q):
    return LEGAL_ANS, {"전자금융거래법시행령:11-4": "④ …별표 1과 같다.", "전자금융거래법시행령:annex-1": "…"}


def _cfg(tid, **kw):
    return {"configurable": {"thread_id": tid, "dart": object(), "http": object(), "legal_runner": fake_runner, **kw}}


@pytest.fixture
def patched(monkeypatch):
    calls = {"route": [], "synth": 0, "verify": 0, "company": []}

    async def passed(claims, ev, judged=None):
        calls["verify"] += 1
        return VerificationResult(passed=True)
    monkeypatch.setattr(G.verifier, "verify", passed)

    async def guide_text(*a, **k):
        return "법령, 기업 분석, 뉴스를 도와드립니다."
    monkeypatch.setattr(G.llm, "text", guide_text)
    return calls


async def test_legal_only_uses_legal_answer(monkeypatch, patched):
    async def r(msgs, mode="auto"):
        return Route(intent="legal", company=None, sub_tasks=[SubTask(agent="legal", instruction="CISO 자격")],
                     clarification=None)
    monkeypatch.setattr(G, "route", r)
    g = G.build_orchestrator(InMemorySaver())
    out = await g.ainvoke({"messages": [HumanMessage("CISO 자격은?")], "mode": "auto"}, config=_cfg("t1"))
    md = out["answer"].markdown
    assert md.startswith("### 핵심 요약") and "시행령 별표 1" in md and patched["verify"] == 1
    assert [c.id for c in out["answer"].citations] == ["전자금융거래법시행령:11-4", "전자금융거래법시행령:annex-1"]
    assert out["messages"][-1].content == md


async def test_company_ambiguous_interrupts_then_resumes(monkeypatch, patched):
    async def r(msgs, mode="auto"):
        return Route(intent="company", company="삼성", sub_tasks=[SubTask(agent="company", instruction="삼성 실적")],
                     clarification=None)
    monkeypatch.setattr(G, "route", r)

    async def cands(dart, name):
        return [{"corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930"},
                {"corp_code": "00164742", "corp_name": "삼성SDI", "stock_code": "006400"}]

    async def profile(dart, code):
        return CorpProfile(corp_code=code, corp_name="삼성전자", stock_code="005930")
    monkeypatch.setattr(G, "listed_candidates", cands)
    monkeypatch.setattr(G.dart_corp, "get_profile", profile)
    seen = []

    async def company(instr, corp, build):
        seen.append(corp.corp_code)
        return SubResult(agent="company", content="사실표 …"), EvidencePart()
    monkeypatch.setattr(G, "run_company", company)

    async def synth(q, results, issues=None, prev=None):
        return [Claim(id="c1", text="삼성전자 실적 요약입니다.")]
    monkeypatch.setattr(G, "synthesize", synth)
    g = G.build_orchestrator(InMemorySaver())
    cfg = _cfg("t2", report_builder=None)
    out = await g.ainvoke({"messages": [HumanMessage("삼성 실적 어때?")], "mode": "auto"}, config=cfg)
    it = out["__interrupt__"][0].value
    assert it["question"] == "어느 기업인가요?" and it["options"] == ["삼성전자 (005930)", "삼성SDI (006400)"]
    out = await g.ainvoke(Command(resume="삼성전자 (005930)"), config=cfg)
    assert seen == ["00126380"] and out["answer"].markdown == "삼성전자 실적 요약입니다."


async def test_chat_intent_guides(monkeypatch, patched):
    async def r(msgs, mode="auto"):
        return Route(intent="chat", company=None, sub_tasks=[], clarification=None)
    monkeypatch.setattr(G, "route", r)
    g = G.build_orchestrator(InMemorySaver())
    out = await g.ainvoke({"messages": [HumanMessage("안녕")], "mode": "auto"}, config=_cfg("t3"))
    assert out["answer"].markdown.startswith("법령, 기업 분석") and patched["verify"] == 0


async def test_failed_claims_are_revised_then_finalized(monkeypatch, patched):
    async def r(msgs, mode="auto"):
        return Route(intent="composite", company=None, sub_tasks=[SubTask(agent="legal", instruction="a"),
                                                                   SubTask(agent="legal", instruction="b")],
                     clarification=None)
    monkeypatch.setattr(G, "route", r)
    n = {"synth": 0}

    async def synth(q, results, issues=None, prev=None):
        n["synth"] += 1
        return [Claim(id="c1", text="결론입니다."), Claim(id="c2", text="과장된 문장입니다."),
                Claim(id="c3", text="근거 없는 문장입니다.")]
    monkeypatch.setattr(G, "synthesize", synth)

    async def fail(claims, ev, judged=None):
        return VerificationResult(passed=False, issues=[
            VerificationIssue(claim_id="c2", type="partial", detail="과장"),
            VerificationIssue(claim_id="c3", type="unsupported", detail="근거 없음")])
    monkeypatch.setattr(G.verifier, "verify", fail)
    g = G.build_orchestrator(InMemorySaver())
    out = await g.ainvoke({"messages": [HumanMessage("x")], "mode": "auto"}, config=_cfg("t4"))
    assert n["synth"] == 1 + G.MAX_REVISIONS
    md = out["answer"].markdown
    assert "결론입니다." in md and "과장된 문장입니다. (근거 불충분)" in md and "근거 없는" not in md


async def test_second_turn_resets_results(monkeypatch, patched):
    async def r(msgs, mode="auto"):
        return Route(intent="legal", company=None, sub_tasks=[SubTask(agent="legal", instruction="q")],
                     clarification=None)
    monkeypatch.setattr(G, "route", r)
    g = G.build_orchestrator(InMemorySaver())
    cfg = _cfg("t5")
    await g.ainvoke({"messages": [HumanMessage("첫 질문")], "mode": "auto"}, config=cfg)
    out = await g.ainvoke({"messages": [HumanMessage("둘째 질문")], "mode": "auto"}, config=cfg)
    assert len(out["results"]) == 1 and len(out["messages"]) == 4


def test_apply_mode_forces_intent():
    r = Route(intent="composite", company="카카오뱅크", clarification=None,
              sub_tasks=[SubTask(agent="company", instruction="실적"), SubTask(agent="legal", instruction="규제")])
    f = apply_mode(r, "legal", "원문")
    assert f.intent == "legal" and [t.instruction for t in f.sub_tasks] == ["규제"]
    assert apply_mode(r, "news", "원문").sub_tasks[0].instruction == "원문"
    assert apply_mode(r, "auto", "원문") is r


async def test_legal_only_is_not_rewritten_on_failure(monkeypatch, patched):
    async def r(msgs, mode="auto"):
        return Route(intent="legal", company=None, sub_tasks=[SubTask(agent="legal", instruction="q")],
                     clarification=None)
    monkeypatch.setattr(G, "route", r)

    async def synth(*a, **k):
        raise AssertionError("법령 단독 답변은 합성·재작성하지 않는다")
    monkeypatch.setattr(G, "synthesize", synth)

    async def fail(claims, ev, judged=None):
        assert claims[0].legal_keys == ["전자금융거래법시행령:11-4", "전자금융거래법시행령:annex-1"]
        return VerificationResult(passed=False, issues=[VerificationIssue(claim_id="legal.2", type="unsupported",
                                                                          detail="x")])
    monkeypatch.setattr(G.verifier, "verify", fail)
    g = G.build_orchestrator(InMemorySaver())
    out = await g.ainvoke({"messages": [HumanMessage("q")], "mode": "auto"}, config=_cfg("t6"))
    md = out["answer"].markdown
    assert "시행령 별표 1에 정해져" in md and "학위와 경력" not in md


def test_route_normalize_fixes_intent_task_mismatch():
    from app.agents.orchestrator.router import normalize
    one = Route(intent="composite", company="x", clarification=None,
                sub_tasks=[SubTask(agent="company", instruction="a")])
    assert normalize(one).intent == "company"
    two = Route(intent="legal", company=None, clarification=None,
                sub_tasks=[SubTask(agent="legal", instruction="a"), SubTask(agent="news", instruction="b"),
                           SubTask(agent="legal", instruction="c")])
    n = normalize(two)
    assert n.intent == "composite" and [t.agent for t in n.sub_tasks] == ["legal", "news"]
    assert normalize(Route(intent="chat", company=None, clarification=None, sub_tasks=[one.sub_tasks[0]])).sub_tasks == []


def test_markdown_cites_filing_for_metric_claims():
    from decimal import Decimal

    from app.agents.orchestrator.synthesize import to_markdown
    from app.domain.models import Citation, Metric
    m = Metric(id="op", label="영업이익(2Q26)", period="2Q26", value=Decimal("1"), unit="KRW", formula="", text="2.6조원")
    cites = [Citation(kind="filing", id="20260814000001", label="반기보고서 (2026.06)",
                      url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260814000001")]
    md = to_markdown([Claim(id="c1", text="영업이익은 {{op.2Q26}}입니다.", metric_ids=["op.2Q26"])],
                     {"op.2Q26": m}, cites)
    assert md.startswith("영업이익은 2.6조원입니다.[1]") and "1. [반기보고서 (2026.06)]" in md


def test_dedupe_phrase():
    from app.agents.orchestrator.synthesize import dedupe_phrase
    assert dedupe_phrase("영업이익률은 42개 분기 중 42개 분기 중 5위입니다") == "영업이익률은 42개 분기 중 5위입니다"
    assert dedupe_phrase("45일 마다 바꾼다") == "45일 마다 바꾼다"
    assert dedupe_phrase("매출과 매출액") == "매출과 매출액"
