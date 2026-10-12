"""멀티턴 스위트: 대화의 2턴부터 라우터가 만든 독립 질문이 정답 독립 질문과 같은 것을 묻는지(후속 질문 해결률).

라우터만 부른다(하위 과업은 돌리지 않는다). 앞 턴의 챗봇 답은 "(답변 생략)"으로 채운다.
의도 일치율도 함께 낸다(composite는 하위 과업 지시문 중 하나라도 같으면 해결로 본다).
"""
from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.orchestrator.router import route
from app.core import budget
from eval import judge


async def run(items: list[dict], p: dict[str, Any]) -> tuple[list[dict], dict]:
    out = []
    for conv in items:
        msgs: list = []
        for i, turn in enumerate(conv["turns"]):
            msgs.append(HumanMessage(content=turn["user"]))
            if i == 0:
                msgs.append(AIMessage(content="(답변 생략)"))
                continue
            usage = budget.start_request()
            t0 = time.perf_counter()
            r = await route(msgs)
            instr = [t.instruction for t in r.sub_tasks]
            same = None
            if instr:
                votes = [await judge.same_question(x, turn["standalone"]) for x in instr]
                same = float(any(v for v in votes if v is not None)) if any(v is not None for v in votes) else None
            elif turn["intent"] == "chat":
                same = float(r.intent == "chat")
            out.append({"id": f"{conv['id']}:{i + 1}", "user": turn["user"], "gold": turn["standalone"],
                        "pred": instr, "intent": turn["intent"], "pred_intent": r.intent,
                        "scores": {"followup_resolved": same if same is not None else 0.0,
                                   "intent_match": float(r.intent == turn["intent"])},
                        "latency_s": round(time.perf_counter() - t0, 2), "cost_usd": round(usage.cost_usd, 6)})
            msgs.append(AIMessage(content="(답변 생략)"))
    return out, {"judge": judge.judge_model()[0] + (" (임시 judge)" if judge.judge_model()[1] else "")}
