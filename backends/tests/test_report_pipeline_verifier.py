"""verify의 LLM 판정 재사용(judged)과 두 판정 호출."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agents import verifier
from app.domain.models import Claim, VerificationIssue


@pytest.mark.asyncio
async def test_judged_cache_rejudges_claim_when_only_evidence_ids_change(monkeypatch):
    support = AsyncMock(side_effect=[
        [VerificationIssue(claim_id="title", type="unsupported", detail="근거와 무관")], []])
    monkeypatch.setattr(verifier, "support_check", support)
    monkeypatch.setattr(verifier, "advice_check", AsyncMock(return_value=[]))
    judged: dict = {}
    first = await verifier.verify([Claim(id="title", text="영업이익 개선")], verifier.Evidence(), judged=judged)
    assert not first.passed
    again = await verifier.verify([Claim(id="title", text="영업이익 개선")], verifier.Evidence(), judged=judged)
    assert not again.passed and support.await_count == 1          # 같은 주장·근거는 다시 판정하지 않는다
    fixed = Claim(id="title", text="영업이익 개선", metric_ids=["m1"])
    result = await verifier.verify([fixed], verifier.Evidence(), judged=judged)
    assert support.await_count == 2
    assert [i.type for i in result.issues] == ["missing_citation"]  # 예전 unsupported 판정을 물려받지 않는다


@pytest.mark.asyncio
async def test_support_and_advice_checks_run_concurrently(monkeypatch):
    both = asyncio.Barrier(2)

    async def support(claims, ev):
        await asyncio.wait_for(both.wait(), 1)
        return [VerificationIssue(claim_id="c", type="partial", detail="일부")]

    async def advice(claims):
        await asyncio.wait_for(both.wait(), 1)
        return [VerificationIssue(claim_id="c", type="investment_advice", detail="권유")]

    monkeypatch.setattr(verifier, "support_check", support)
    monkeypatch.setattr(verifier, "advice_check", advice)
    result = await verifier.verify([Claim(id="c", text="매수 권유")], verifier.Evidence())
    assert [i.type for i in result.issues] == ["partial", "investment_advice"]
