"""OpenDART 모듈: 캐시 규칙, 상태 코드 처리, 보고서명·주식 수 파서."""
from __future__ import annotations

import time

from app.core.dart import shares
from app.core.dart.client import DartNoData


class FakeDart:
    """dart.json만 흉내 낸다. answers는 호출마다 하나씩 꺼내 쓰고, 예외면 던진다."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    async def json(self, endpoint, **params):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


async def test_share_counts_empty_answer_expires(temp_cache):
    # 보고서 제출 직후 주식 총수가 아직 013이면 짧게만 기억하고, 데이터가 들어오면 다시 읽는다
    rows = [{"se": "보통주", "istc_totqy": "1,000", "tesstk_co": "100", "distb_stock_co": "900", "rcept_no": "r1"}]
    dart = FakeDart(DartNoData("013", "없음"), {"status": "000", "list": rows})
    assert await shares.share_counts(dart, "c", 2026, "11014") is None
    key = "stockTotqySttus:c:2026:11014"
    row = temp_cache._run("SELECT expires_at FROM kv WHERE namespace='dart.sh' AND key=?", (key,))
    assert row and row[0][0] is not None and row[0][0] - time.time() <= 300
    await temp_cache.set("dart.sh", key, [], ttl_s=-1)                    # 5분이 지난 것처럼
    got = await shares.share_counts(dart, "c", 2026, "11014")
    assert got is not None and got.common_out == 900 and dart.calls == 2
    assert await shares.share_counts(dart, "c", 2026, "11014") is not None and dart.calls == 2   # 정상 값은 계속 쓴다
