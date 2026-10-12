"""라우터: 최근 대화로 의도(legal·company·news·composite·chat)와 하위 과업을 정한다.

- 입력은 최근 10개 메시지다(그보다 앞의 대화는 쓰지 않는다. 2026-10-12 결정).
- 후속 질문("그럼 위반하면 과태료는?")은 앞 대화의 대상을 넣은 독립 질문으로 바꿔 SubTask.instruction에 넣는다.
- 드롭다운 mode가 auto가 아니면 의도를 그 값으로 고정한다.
"""
from __future__ import annotations

from typing import Literal

from langchain_core.messages import AnyMessage
from pydantic import BaseModel

from app.core import llm

RECENT = 10
Intent = Literal["legal", "company", "news", "composite", "chat"]


class SubTask(BaseModel):
    agent: Literal["legal", "company", "news"]
    instruction: str             # 대화 맥락의 지시어를 풀어 쓴 독립 질문


class Route(BaseModel):
    intent: Intent
    company: str | None          # 질문이 가리키는 기업명(사용자가 쓴 그대로). 없으면 null
    sub_tasks: list[SubTask]     # 단일 의도면 1개, composite면 2–3개, chat이면 0개
    clarification: str | None    # 의도나 대상 기업을 정할 수 없을 때 되물을 질문


ROUTER_SYSTEM = """너는 금융 리서치 챗봇의 라우터다. 대화의 마지막 사용자 질문을 아래 의도 중 하나로 분류하고 하위 과업을 만든다.

- legal: 전자금융 법령(전자금융거래법, 같은 법 시행령, 전자금융감독규정, 같은 규정 시행세칙)의 의무·요건·절차·제재를 묻는 질문.
- company: 특정 상장사의 실적·재무·밸류에이션·주가·주주환원을 묻는 질문.
- news: 특정 기업의 최근 뉴스·이슈·동향을 묻는 질문.
- composite: 위 셋 중 둘 이상이 필요한 질문(예: "카카오뱅크 실적과 관련 규제 리스크").
- chat: 인사, 이 서비스의 기능을 묻는 질문, 위 범위 밖의 질문.

규칙:
- sub_tasks: legal·company·news면 해당 agent 1개, composite면 2~3개, chat이면 빈 리스트.
- instruction: 앞 대화 없이도 이해되는 독립 질문. "그럼", "거기", "그 회사" 같은 지시어를 앞 대화의 대상으로 바꿔 쓴다.
- company: company·news 과업이 있으면 대상 기업명(사용자가 쓴 표기 그대로, 앞 대화에서 이어지면 그 기업명). 없으면 null.
- clarification: company·news 과업인데 대상 기업을 대화에서 알 수 없을 때, 또는 질문 뜻을 정할 수 없을 때만 되물을 질문 한 문장. 아니면 null."""


def recent_text(messages: list[AnyMessage], n: int = RECENT) -> str:
    lines = []
    for m in messages[-n:]:
        role = "사용자" if m.type == "human" else "챗봇"
        content = m.content if isinstance(m.content, str) else str(m.content)
        lines.append(f"[{role}] {content[:2000]}")
    return "\n".join(lines)


def _check(r: Route) -> str | None:
    if r.intent == "chat":
        return None if not r.sub_tasks else "chat이면 sub_tasks는 빈 리스트"
    if r.intent == "composite":
        return None if 2 <= len(r.sub_tasks) <= 3 else "composite면 sub_tasks 2~3개"
    if len(r.sub_tasks) != 1 or r.sub_tasks[0].agent != r.intent:
        return f"{r.intent}면 agent가 {r.intent}인 sub_task 1개"
    return None


def apply_mode(r: Route, mode: str, question: str) -> Route:
    """드롭다운으로 의도를 고정했을 때: 그 agent의 과업만 남기고, 없으면 질문 그대로 하나 만든다."""
    if mode not in ("legal", "company", "news"):
        return r
    tasks = [t for t in r.sub_tasks if t.agent == mode] or [SubTask(agent=mode, instruction=question)]
    return r.model_copy(update={"intent": mode, "sub_tasks": tasks[:1],
                                "clarification": r.clarification if mode != "legal" else None})


async def route(messages: list[AnyMessage], mode: str = "auto") -> Route:
    r = await llm.structured(Route, [{"role": "user", "content": recent_text(messages)}], system=ROUTER_SYSTEM,
                             effort="low", task="chat.route", validate=_check)
    question = messages[-1].content if messages and isinstance(messages[-1].content, str) else ""
    return apply_mode(r, mode, question)
