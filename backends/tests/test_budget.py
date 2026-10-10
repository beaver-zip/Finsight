import pytest

from app.core import budget
from app.settings import get_settings


def test_claude_cost_tiers():
    # Haiku 5.5: 10만 토큰 이하 0.10/0.50
    assert budget.claude_cost("claude-haiku-5-5", 100_000, 0) == pytest.approx(0.01)
    assert budget.claude_cost("claude-haiku-5-5", 1_000, 1_000_000) == pytest.approx(0.5001)
    # 프롬프트 10만 초과면 상위 단가(0.50)
    assert budget.claude_cost("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)
    assert budget.claude_cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)
    assert budget.claude_cost("claude-haiku-5-5", 100_000, 0, batch=True) == pytest.approx(0.005)


def test_tavily_free_credits_first():
    budget.set_tavily_free_remaining(2)
    assert budget.tavily_cost_for(1) == 0
    assert budget.tavily_cost_for(2) == pytest.approx(0.008)
    budget.set_tavily_free_remaining(None)
    assert budget.tavily_cost_for(3) == pytest.approx(0.024)


async def test_budget_blocks(temp_cache):
    await budget.record("anthropic", get_settings().budget_usd + 0.01)
    with pytest.raises(budget.BudgetExceeded):
        await budget.check_budget()


async def test_search_credit_cap(temp_cache):
    await budget.record("tavily", 0, credits=get_settings().tavily_max_credits)
    with pytest.raises(budget.SearchCreditsExceeded):
        await budget.check_search_credits()


async def test_cache_ttl(temp_cache):
    await temp_cache.set("ns", "k", {"a": 1}, ttl_s=-1)
    assert await temp_cache.get("ns", "k") is None
    await temp_cache.set("ns", "k", {"a": 1})
    assert await temp_cache.get("ns", "k") == {"a": 1}
