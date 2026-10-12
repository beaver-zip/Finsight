"""유료 API 비용 계산과 예산 차단.

- Claude: usage 토큰 × 단가표
- OpenRouter: 응답 usage.cost(USD)
- Tavily: usage.credits. 이번 결제 주기의 남은 무료(플랜) 크레딧 안이면 0달러, 넘으면 1크레딧당 0.008달러
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.cache import get_cache
from app.settings import get_settings

TAVILY_USD_PER_CREDIT = 0.008


class BudgetExceeded(RuntimeError):
    """누적 비용이 BUDGET_USD를 넘어 새 유료 호출을 막았다."""


class SearchCreditsExceeded(RuntimeError):
    """누적 Tavily 크레딧이 TAVILY_MAX_CREDITS에 닿았다."""


class TokenBudgetExceeded(RuntimeError):
    """요청 하나의 Claude 토큰 합이 REQUEST_TOKEN_BUDGET을 넘었다."""


# USD / 100만 토큰: (입력, 출력, 캐시 쓰기 5분, 캐시 읽기)
_PRICES: dict[str, list[tuple[int | None, tuple[float, float, float, float]]]] = {
    # (프롬프트 토큰 상한, 단가). 상한 None = 그 이상 전부
    "claude-haiku-5-5": [(100_000, (0.10, 0.50, 0.125, 0.01)), (None, (0.50, 2.50, 0.625, 0.05))],
    "claude-sonnet-5-5": [(None, (2.0, 10.0, 2.50, 0.10))],
}


def claude_cost(model: str, input_tokens: int, output_tokens: int,
                cache_write: int = 0, cache_read: int = 0, *, batch: bool = False) -> float:
    tiers = _PRICES.get(model)
    if tiers is None:
        raise KeyError(f"단가표에 없는 모델: {model}")
    prompt = input_tokens + cache_write + cache_read
    price = next(p for limit, p in tiers if limit is None or prompt <= limit)
    i, o, cw, cr = price
    cost = (input_tokens * i + output_tokens * o + cache_write * cw + cache_read * cr) / 1_000_000
    return cost * 0.5 if batch else cost


async def check_budget() -> None:
    s = get_settings()
    total = await get_cache().total_cost()
    if total >= s.budget_usd:
        raise BudgetExceeded(f"누적 비용 {total:.4f}달러가 BUDGET_USD {s.budget_usd}달러에 닿았습니다")


async def record(service: str, cost_usd: float, **fields) -> None:
    await get_cache().record_cost({"service": service, "cost_usd": cost_usd, **fields})


# ---- Tavily 무료 크레딧 상태 (프로세스 시작 시 /usage로 채운다) ----
@dataclass
class _TavilyFree:
    remaining: int | None = None  # None = 모름 → 전부 과금으로 센다


_tavily_free = _TavilyFree()


def set_tavily_free_remaining(n: int | None) -> None:
    _tavily_free.remaining = n


def tavily_cost_for(credits: int) -> float:
    """남은 무료 크레딧을 먼저 쓰고, 넘는 크레딧만 과금한다."""
    rem = _tavily_free.remaining
    if rem is None:
        return credits * TAVILY_USD_PER_CREDIT
    free = min(rem, credits)
    _tavily_free.remaining = rem - free
    return (credits - free) * TAVILY_USD_PER_CREDIT


async def check_search_credits(needed: int = 1) -> None:
    s = get_settings()
    used = await get_cache().total_credits("tavily")
    if used + needed > s.tavily_max_credits:
        raise SearchCreditsExceeded(f"Tavily 누적 {used}크레딧, 상한 {s.tavily_max_credits}")


# ---- 요청 단위 토큰·비용 집계 ----
@dataclass
class RequestUsage:
    tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    by_service: dict[str, float] = field(default_factory=dict)
    exceeded: bool = False          # 토큰 상한에 걸려 LLM 호출을 멈춘 적이 있다(노드가 예외를 삼켜도 남는다)


_request_usage: contextvars.ContextVar[RequestUsage | None] = contextvars.ContextVar(
    "request_usage", default=None)


def start_request() -> RequestUsage:
    u = RequestUsage()
    _request_usage.set(u)
    return u


def current_request() -> RequestUsage | None:
    return _request_usage.get()


def add_request_usage(service: str, tokens: int, cost: float) -> None:
    u = _request_usage.get()
    if u is None:
        return
    u.tokens += tokens
    u.cost_usd += cost
    u.calls += 1
    u.by_service[service] = u.by_service.get(service, 0.0) + cost


def check_request_tokens() -> None:
    u = _request_usage.get()
    if u is not None and u.tokens >= get_settings().request_token_budget:
        u.exceeded = True
        raise TokenBudgetExceeded(f"요청 토큰 {u.tokens} ≥ {get_settings().request_token_budget}")


def token_budget_event(u: RequestUsage | None) -> dict | None:
    """상한에 걸린 요청이면 SSE error(TOKEN_BUDGET) 내용. 결과는 그때까지 만든 것만 담긴다."""
    if u is None or not u.exceeded:
        return None
    return {"code": "TOKEN_BUDGET", "partial": True,
            "message": f"요청 하나의 Claude 토큰이 상한({get_settings().request_token_budget:,})을 넘어 이후 LLM 호출을 멈췄습니다"}


def to_decimal(x: float) -> Decimal:
    return Decimal(str(round(x, 6)))
