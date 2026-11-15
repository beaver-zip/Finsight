"""POST /chat SSE: 빈 요청 BAD_REQUEST, 되묻기 interrupt 뒤 종료, 같은 thread_id의 resume으로 final."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph

from app.agents.orchestrator import graph as G
from app.agents.orchestrator.router import Route, SubTask
from app.api import chat
from app.deps import get_state
from app.domain.models import CitedSpan, LegalAnswer, LegalPoint, VerificationResult


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        head, data = block.split("\n", 1)
        out.append((head.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def client_for(st) -> TestClient:
    app = FastAPI()
    app.include_router(chat.router, prefix="/chat")
    app.dependency_overrides[get_state] = lambda: st
    return TestClient(app)


def test_empty_request_is_bad_request():
    with client_for(NS(dart=None, http=None, legal_runner=None)) as c:     # 그래프는 부르지 않는다
        got = events(c.post("/chat", json={"thread_id": "t"}).text)
    assert got == [("error", {"code": "BAD_REQUEST", "message": "message나 resume이 필요합니다", "partial": False})]


def test_clarification_interrupts_then_resume_streams_final(monkeypatch):
    async def r(msgs, mode="auto"):
        ask = "어느 법령인가요?" if len(msgs) == 1 else None
        return Route(intent="legal", company=None, clarification=ask,
                     sub_tasks=[SubTask(agent="legal", instruction="CISO 자격")])
    monkeypatch.setattr(G, "route", r)

    async def passed(claims, ev, judged=None):
        return VerificationResult(passed=True)
    monkeypatch.setattr(G.verifier, "verify", passed)
    ans = LegalAnswer(answerable=True, points=[LegalPoint(
        text="정보보호최고책임자의 자격은 시행령 별표 1에 정해져 있습니다.",
        citations=[CitedSpan(source="전자금융거래법시행령:11-4", paragraph_index=4, cited_text="…별표 1과 같다.")])])

    sub = StateGraph(dict)              # make_runner처럼 따로 config를 넘겨 ainvoke하는 하위 그래프
    sub.add_node("a", lambda s: {"q": s["q"]})
    sub.add_edge(START, "a")
    sub = sub.compile()

    async def runner(q):
        await sub.ainvoke({"q": q}, config={"configurable": {}})
        return ans, {"전자금융거래법시행령:11-4": "④ …별표 1과 같다."}
    saver = InMemorySaver()
    st = NS(chat_graph=G.build_orchestrator(saver), dart=object(), http=object(), legal_runner=runner)
    with client_for(st) as c:
        first = events(c.post("/chat", json={"thread_id": "t1", "message": "CISO 자격은?"}).text)
        assert first[-1] == ("interrupt", {"question": "어느 법령인가요?", "options": []})
        assert "final" not in [e for e, _ in first]
        second = events(c.post("/chat", json={"thread_id": "t1", "resume": "전자금융거래법"}).text)
    final = [d for e, d in second if e == "final"]
    assert len(final) == 1 and second[-1][0] == "final"
    assert "시행령 별표 1" in final[0]["answer"]["markdown"]
    # 턴 끝과 되묻기 때만 상위 체크포인트를 남기고, 하위 그래프 상태는 남기지 않는다(기본값이면 상위 11개, 하위 3개)
    assert {ns: len(v) for ns, v in saver.storage["t1"].items() if v} == {"": 2}
