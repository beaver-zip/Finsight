"""생성 모델(Claude) 호출. 노드는 structured()·cited()만 쓰고, 뉴스·웹 에이전트는 agent_model()을 쓴다.

- temperature·top_p·top_k는 보내지 않는다(기본값과 다르면 400).
- 프리필을 쓰지 않는다. 메시지 목록은 항상 user 턴으로 끝난다.
- 응답은 thinking 블록으로 시작할 수 있으므로 content는 type으로 골라 읽는다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Literal, TypeVar

from anthropic import AsyncAnthropic
from anthropic.types import Message
from pydantic import BaseModel

from app.core import budget
from app.core.cache import get_cache, make_key
from app.settings import get_settings, require

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
Role = Literal["default", "strong"]  # default=Haiku 5.5, strong=Sonnet 5.5(비교 실험 전용). judge는 eval/metrics/judge.py가 OpenRouter로 부른다
Effort = Literal["low", "medium", "high"]

MAX_TOKENS: dict[Effort, int] = {"low": 4_000, "medium": 8_000, "high": 16_000}
CLAUDE_CONCURRENCY = 8
CLAUDE_TIMEOUT_S = 120.0

_client: AsyncAnthropic | None = None
_sem = asyncio.Semaphore(CLAUDE_CONCURRENCY)

try:  # LangSmith 트레이스에 SDK 직접 호출을 넣는다
    from langsmith import traceable
except Exception:  # pragma: no cover
    def traceable(*a, **k):  # type: ignore
        def deco(f):
            return f
        return deco if not (a and callable(a[0])) else a[0]


class StructuredOutputError(RuntimeError):
    pass


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        s = get_settings()
        # 이 작업 공간은 ANTHROPIC_BASE_URL을 다른 용도로 설정해 두므로 공식 엔드포인트를 명시한다
        _client = AsyncAnthropic(api_key=require(s.anthropic_api_key, "ANTHROPIC_API_KEY"),
                                 base_url="https://api.anthropic.com",
                                 timeout=CLAUDE_TIMEOUT_S, max_retries=2)
    return _client


def model_for(role: Role) -> str:
    s = get_settings()
    return s.llm_model_strong if role == "strong" else s.llm_model


async def _account(model: str, usage: Any, *, task: str) -> float:
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = budget.claude_cost(model, usage.input_tokens, usage.output_tokens, cw, cr)
    await budget.record("anthropic", cost, model=model, input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens, cache_write_tokens=cw,
                        cache_read_tokens=cr, meta={"task": task})
    budget.add_request_usage("anthropic", usage.input_tokens + usage.output_tokens + cw + cr, cost)
    return cost


async def _guard() -> None:
    await budget.check_budget()
    budget.check_request_tokens()


def text_of(msg: Message) -> str:
    return "".join(b.text for b in msg.content if b.type == "text")


@traceable(run_type="llm", name="claude.structured")
async def structured(schema: type[T], messages: list[dict], *, role: Role = "default",
                     effort: Effort = "low", system: list[dict] | str | None = None,
                     max_tokens: int | None = None, task: str = "structured",
                     validate: Callable[[T], str | None] | None = None, thinking: bool = True) -> T:
    """구조화 출력. 스키마 위반·개수 검사 실패·refusal → 같은 설정으로 1회, max_tokens 중단 →
    max_tokens를 2배로 1회 다시 요청 → 그래도 실패하면 StructuredOutputError."""
    assert messages and messages[-1]["role"] == "user", "메시지는 user 턴으로 끝나야 한다(프리필 금지)"
    model = model_for(role)
    max_tokens = max_tokens or MAX_TOKENS[effort]
    s = get_settings()
    key_parts: list[Any] = ["structured", model, effort, system, messages, schema.model_json_schema()]
    if not thinking:
        key_parts.append("no-thinking")
    cache_key = make_key(*key_parts)
    if s.llm_cache:
        hit = await get_cache().get("llm", cache_key)
        if hit is not None:
            return schema.model_validate(hit)

    retried_content = retried_tokens = False
    last_err = ""
    while True:
        await _guard()
        kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, messages=messages,
                                      output_format=schema, output_config={"effort": effort})
        if system:
            kwargs["system"] = system
        if not thinking:
            # 주어진 사실로 문장을 채우는 작업(본문 작성 등)은 생각 단계 없이도 품질이 같고 3배 빠르다
            kwargs["thinking"] = {"type": "disabled"}
        async with _sem:
            if max_tokens > MAX_TOKENS["medium"]:
                # 긴 출력(본문 작성 등)은 스트리밍으로 받는다. 비스트리밍은 한 번의 읽기 제한(120초)에 걸린다
                async with client().messages.stream(**kwargs) as st:
                    resp = await st.get_final_message()
            else:
                resp = await client().messages.parse(**kwargs)
        await _account(model, resp.usage, task=task)

        if resp.stop_reason == "max_tokens":
            if retried_tokens:
                raise StructuredOutputError(f"{task}: max_tokens({max_tokens})에서 두 번 중단")
            retried_tokens, max_tokens = True, max_tokens * 2
            continue
        parsed = resp.parsed_output
        problem = None
        if resp.stop_reason == "refusal":
            problem = "refusal"
        elif parsed is None:
            problem = "스키마 파싱 실패"
        elif validate is not None:
            problem = validate(parsed)
        if problem is None:
            if s.llm_cache:
                await get_cache().set("llm", cache_key, parsed.model_dump(mode="json"))
            return parsed
        last_err = problem
        if retried_content:
            raise StructuredOutputError(f"{task}: {last_err}")
        retried_content = True
        log.warning("structured %s 재시도: %s", task, problem)


@traceable(run_type="llm", name="claude.cited")
async def cited(question: str, docs: list[dict], *, role: Role = "default",
                effort: Effort = "medium", system: str | None = None,
                max_tokens: int = 16_000, task: str = "cited") -> Message:
    """search_result 블록 + citations. output_config.format은 함께 보내지 않는다.
    docs는 key 순으로 정렬된 search_result 블록이고, 마지막 블록에만 cache_control을 건다."""
    model = model_for(role)
    blocks = [dict(d) for d in docs]
    if blocks:
        blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    messages = [{"role": "user", "content": [*blocks, {"type": "text", "text": question}]}]
    s = get_settings()
    cache_key = make_key("cited", model, effort, system, messages)
    if s.llm_cache:
        hit = await get_cache().get("llm", cache_key)
        if hit is not None:
            return Message.model_validate(hit)
    await _guard()
    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, messages=messages,
                                  output_config={"effort": effort})
    if system:
        kwargs["system"] = system
    async with _sem:
        resp = await client().messages.create(**kwargs)
    await _account(model, resp.usage, task=task)
    if s.llm_cache:
        await get_cache().set("llm", cache_key, resp.model_dump(mode="json"))
    return resp


@traceable(run_type="llm", name="claude.text")
async def text(messages: list[dict], *, role: Role = "default", effort: Effort = "low",
               system: str | list[dict] | None = None, max_tokens: int | None = None,
               task: str = "text") -> str:
    """구조 없이 텍스트만 받는 호출(대화 요약 등)."""
    assert messages and messages[-1]["role"] == "user"
    model = model_for(role)
    s = get_settings()
    cache_key = make_key("text", model, effort, system, messages)
    if s.llm_cache:
        hit = await get_cache().get("llm", cache_key)
        if hit is not None:
            return hit
    await _guard()
    kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens or MAX_TOKENS[effort],
                                  messages=messages, output_config={"effort": effort})
    if system:
        kwargs["system"] = system
    async with _sem:
        resp = await client().messages.create(**kwargs)
    await _account(model, resp.usage, task=task)
    out = text_of(resp)
    if s.llm_cache:
        await get_cache().set("llm", cache_key, out)
    return out


def agent_model(effort: Effort = "medium"):
    """뉴스·웹 에이전트용 ChatAnthropic. temperature는 지정하지 않는다."""
    from langchain_anthropic import ChatAnthropic

    s = get_settings()
    return ChatAnthropic(model=s.llm_model, api_key=require(s.anthropic_api_key, "ANTHROPIC_API_KEY"),
                         base_url="https://api.anthropic.com", max_tokens=MAX_TOKENS[effort],
                         timeout=CLAUDE_TIMEOUT_S, max_retries=2,
                         model_kwargs={"output_config": {"effort": effort}})
