"""복합 과업 스위트: 채팅 오케스트레이터에 과업 질문을 넣고 체크리스트로 채점한다.

- 과업 성공 = judge가 체크리스트 전 항목을 충족으로 판정. 항목 충족률도 함께 낸다(무작위 10과업은 사람이 재판정).
- 도구 호출 유효율 = 하위 과업 중 오류 없이 끝난 비율. 호출 수 = 과업당 유료 API 호출 수(Claude·임베딩·검색).
- 되묻기(interrupt)가 나오면 과업의 기업명이 든 선택지로 자동 재개한다(없으면 기업명을 그대로 답한다).
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.legal.graph import build_legal_graph, make_runner
from app.agents.legal.tools import LawTools
from app.agents.orchestrator.graph import build_orchestrator
from app.core import budget
from app.core.dart.client import DartClient
from eval import judge


async def _answer(graph, cfg: dict, question: str, company: str | None) -> dict:
    out = await graph.ainvoke({"messages": [HumanMessage(content=question)], "mode": "auto"}, config=cfg)
    for _ in range(3):
        its = out.get("__interrupt__")
        if not its:
            break
        v = its[0].value if isinstance(its[0].value, dict) else {}
        opts = v.get("options") or []
        pick = next((o for o in opts if company and company in o), opts[0] if opts else (company or ""))
        out = await graph.ainvoke(Command(resume=pick), config=cfg)
    return out


async def run(items: list[dict], p: dict[str, Any]) -> tuple[list[dict], dict]:
    from app.api.report import build_report
    from app.deps import AppState

    tools = await LawTools.open()
    out = []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            dart = DartClient(http, concurrency=2)
            runner = make_runner(build_legal_graph(), tools)
            from app.agents.company.graph import build_company_graph

            st = AppState(http=http, dart=dart, company_graph=build_company_graph(), legal_runner=runner)
            graph = build_orchestrator(InMemorySaver())
            for it in items:
                usage = budget.start_request()
                t0 = time.perf_counter()

                async def builder(code: str, _st=st) -> dict:
                    return await build_report(_st, code)
                cfg = {"configurable": {"thread_id": str(uuid.uuid4()), "dart": dart, "http": http,
                                        "legal_runner": runner, "report_builder": builder}, "recursion_limit": 50}
                try:
                    res = await _answer(graph, cfg, it["question"], it.get("company"))
                except Exception as e:  # noqa: BLE001
                    out.append({"id": it["id"], "error": f"{type(e).__name__}: {e}", "scores": {"success": 0.0},
                                "latency_s": round(time.perf_counter() - t0, 2), "cost_usd": round(usage.cost_usd, 6)})
                    continue
                ans = res.get("answer")
                md = ans.markdown if ans else ""
                checks = await judge.checklist(md, it["checklist"])
                results = res.get("results") or []
                out.append({"id": it["id"], "kind": it["kind"], "answer": md,
                            "route": ans.route.model_dump() if ans and ans.route else None, "checks": checks,
                            "scores": {"success": None if checks is None else float(all(checks)),
                                       "checklist_rate": None if checks is None else sum(checks) / len(checks),
                                       "tool_valid": (sum(1 for r in results if not r.error) / len(results))
                                       if results else None,
                                       "calls": float(usage.calls)},
                            "latency_s": round(time.perf_counter() - t0, 2), "cost_usd": round(usage.cost_usd, 6)})
    finally:
        await tools.close()
    return out, {"judge": judge.judge_model()[0] + (" (임시 judge)" if judge.judge_model()[1] else ""),
                 "human_recheck": "무작위 10과업은 사람이 재판정한다(eval/datasets/agent_recheck.v1.jsonl)"}
