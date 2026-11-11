"""동시에 시작한 검색의 크레딧 상한·캐시·오류 후 복구."""
import asyncio

import httpx
import pytest

from app.core import budget
from app.core.search import tavily
from app.settings import get_settings


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.setattr(tavily, "_auth", lambda: {})
    monkeypatch.setattr(tavily, "_sem", asyncio.Semaphore(1))
    budget.set_tavily_free_remaining(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("same_query", [False, True])
async def test_parallel_searches_respect_one_remaining_credit(auth, monkeypatch, temp_cache, same_query):
    monkeypatch.setattr(get_settings(), "tavily_max_credits", 1)
    # 모든 요청이 캐시 miss를 본 뒤 함께 계속한다. 실제 시간 지연에 의존하지 않는다.
    all_started = asyncio.Event()
    get = temp_cache.get
    started = 0
    async def cache_get(*args):
        nonlocal started
        result = await get(*args)
        started += 1
        if started >= 8:
            all_started.set()
        await all_started.wait()
        return result
    monkeypatch.setattr(temp_cache, "get", cache_get)
    calls = []
    async def response(req):
        calls.append(req)
        return httpx.Response(200, json={"results": [], "usage": {"credits": 1}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
        results = await asyncio.gather(*(tavily.search("same" if same_query else str(i), http=http) for i in range(8)),
                                       return_exceptions=True)
    assert len(calls) == 1
    assert await temp_cache.total_credits("tavily") == 1
    assert sum(isinstance(r, budget.SearchCreditsExceeded) for r in results) == (0 if same_query else 7)


@pytest.mark.asyncio
async def test_failed_search_releases_guard_and_is_not_charged(auth, temp_cache):
    responses = iter([httpx.Response(503), httpx.Response(200, json={"results": [], "usage": {"credits": 1}})])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: next(responses))) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await tavily.search("retry", http=http)
        assert await temp_cache.total_credits("tavily") == 0
        assert (await tavily.search("retry", http=http))["results"] == []
    assert await temp_cache.total_credits("tavily") == 1
