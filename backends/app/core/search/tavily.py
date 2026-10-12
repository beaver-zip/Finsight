"""Tavily /search 호출. 응답은 (질의, topic, time_range, search_depth, max_results, …)로 7일 캐시한다.

- search_depth는 항상 "basic"(1크레딧)으로 명시하고 auto_parameters는 켜지 않는다.
- include_usage=true로 받은 usage.credits를 비용 장부에 적는다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx

from app.core import budget
from app.core.cache import get_cache, make_key
from app.settings import get_settings, require

log = logging.getLogger(__name__)
BASE = "https://api.tavily.com"
WEEK = 7 * 86_400
_sem = asyncio.Semaphore(4)

Topic = Literal["news", "general"]
TimeRange = Literal["day", "week", "month", "year"]


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {require(get_settings().tavily_api_key, 'TAVILY_API_KEY')}"}


async def init_free_credits(http: httpx.AsyncClient | None = None) -> int | None:
    """서버·평가 러너 시작 시 1회: 이번 결제 주기의 남은 플랜 크레딧을 읽어 장부 계산에 쓴다."""
    own = http is None
    http = http or httpx.AsyncClient(timeout=10)
    try:
        r = await http.get(f"{BASE}/usage", headers=_auth())
        r.raise_for_status()
        acc = r.json().get("account", {})
        remaining = max(0, int(acc.get("plan_limit") or 0) - int(acc.get("plan_usage") or 0))
    except Exception as e:  # 못 읽으면 전부 과금으로 센다
        log.warning("Tavily /usage 조회 실패: %s", e)
        remaining = None
    finally:
        if own:
            await http.aclose()
    budget.set_tavily_free_remaining(remaining)
    return remaining


async def search(query: str, *, topic: Topic = "news", time_range: TimeRange | None = "month",
                 max_results: int = 20, include_raw_content: bool = True, language: str | None = "ko",
                 country: str | None = None, include_domains: list[str] | None = None,
                 include_published_date: bool = False, http: httpx.AsyncClient | None = None) -> dict:
    body: dict[str, Any] = {"query": query, "topic": topic, "search_depth": "basic", "max_results": max_results,
                            "include_usage": True, "include_answer": False}
    if time_range:
        body["time_range"] = time_range
    if include_raw_content:
        body["include_raw_content"] = "text"
    if language:
        body["language"] = language
    if country and topic == "general":
        body["country"] = country
    if include_domains:
        body["include_domains"] = include_domains
    if include_published_date or topic == "news":
        body["include_published_date"] = True

    cache = get_cache()
    key = make_key(body)
    hit = await cache.get("tavily", key)
    if hit is not None:
        return hit

    await budget.check_budget()
    await budget.check_search_credits(1)
    own = http is None
    http = http or httpx.AsyncClient(timeout=20)
    try:
        async with _sem:
            r = await http.post(f"{BASE}/search", headers=_auth(), json=body, timeout=20)
        r.raise_for_status()
        data = r.json()
    finally:
        if own:
            await http.aclose()
    credits = int((data.get("usage") or {}).get("credits") or 1)
    cost = budget.tavily_cost_for(credits)
    await budget.record("tavily", cost, credits=credits, meta={"query": query, "topic": topic})
    budget.add_request_usage("tavily", 0, cost)
    await cache.set("tavily", key, data, ttl_s=WEEK)
    return data
