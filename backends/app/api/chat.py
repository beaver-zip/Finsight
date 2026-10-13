"""POST /chat: 채팅 오케스트레이터를 SSE로 돌린다.

이벤트: node(노드 끝), progress(진행 문구), interrupt(되묻기: question, options), final(answer, cost_usd),
error(code, message, partial). 되묻기에 답할 때는 같은 thread_id로 resume에 답을 넣어 다시 부른다.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from app.api.report import build_report, error_code
from app.core import budget
from app.deps import AppState, get_state

log = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    thread_id: str
    message: str | None = None
    mode: Literal["auto", "legal", "company", "news"] = "auto"
    resume: str | None = None              # 되묻기에 대한 답(선택지 문자열 또는 자유 입력)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def chat_config(st: AppState, thread_id: str) -> dict:
    async def report_builder(corp_code: str) -> dict:
        return await build_report(st, corp_code)
    return {"configurable": {"thread_id": thread_id, "dart": st.dart, "http": st.http,
                             "legal_runner": st.legal_runner, "report_builder": report_builder},
            "recursion_limit": 50}


@router.post("", summary="채팅(SSE)")
async def chat(req: ChatRequest, st: AppState = Depends(get_state)):
    async def gen():
        cfg = chat_config(st, req.thread_id)
        if req.resume is not None:
            inp = Command(resume=req.resume)
        elif req.message:
            inp = {"messages": [HumanMessage(content=req.message)], "mode": req.mode}
        else:
            yield _sse("error", {"code": "BAD_REQUEST", "message": "message나 resume이 필요합니다", "partial": False})
            return
        usage = budget.start_request()
        try:
            async for ns, mode, chunk in st.chat_graph.astream(inp, config=cfg, stream_mode=["updates", "custom"],
                                                                subgraphs=True):
                if mode == "custom" and isinstance(chunk, dict) and chunk.get("type") == "progress":
                    yield _sse("progress", {"stage": chunk["stage"], "detail": chunk["detail"]})
                elif mode == "updates":
                    for node, upd in chunk.items():
                        if node == "__interrupt__":
                            for it in upd:
                                v = it.value if isinstance(it.value, dict) else {"question": str(it.value)}
                                yield _sse("interrupt", {"question": v.get("question"),
                                                         "options": v.get("options") or []})
                            return
                        yield _sse("node", {"ns": ["chat", *[n.split(":")[0] for n in ns]], "node": node,
                                            "status": "end"})
            state = await st.chat_graph.aget_state(cfg)
            vals = state.values
            for e in vals.get("errors") or []:
                yield _sse("error", {"code": "PARTIAL", "message": e, "partial": True})
            if (tb := budget.token_budget_event(usage)):
                yield _sse("error", tb)
            ans = vals.get("answer")
            yield _sse("final", {"answer": ans.model_dump(mode="json") if ans else None,
                                 "cost_usd": round(usage.cost_usd, 5)})
        except Exception as e:  # noqa: BLE001
            log.exception("chat 실패")
            yield _sse("error", {"code": error_code(e), "message": f"{type(e).__name__}: {e}", "partial": False})

    return StreamingResponse(gen(), media_type="text/event-stream")
