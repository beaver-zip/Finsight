"""임베딩: 배치는 동시성 2까지 함께 보내고, 순서와 캐시는 그대로다."""
from __future__ import annotations

import asyncio
import json

import httpx
import numpy as np

from app.core import embeddings


async def test_embed_sends_batches_concurrently_and_keeps_order(monkeypatch, temp_cache):
    live = peak = 0
    sizes = []

    async def handler(req):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        batch = json.loads(req.content)["input"]
        sizes.append(len(batch))
        data = [{"index": i, "embedding": [1.0, float(t.split()[1])]} for i, t in enumerate(batch)]
        return httpx.Response(200, json={"data": list(reversed(data)), "usage": {"cost": 0.0}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(embeddings, "_client", lambda: http)
    monkeypatch.setattr(embeddings, "require", lambda value, name: "K")
    texts = [f"t {i}" for i in range(150)]
    m = await embeddings.embed(texts)
    assert sorted(sizes) == [22, 64, 64] and peak == 2              # 세마포어 2만큼 함께 보낸다
    assert m.shape == (150, 2) and np.allclose(np.linalg.norm(m, axis=1), 1.0)   # 정규화
    assert np.allclose(m[:, 1] / m[:, 0], np.arange(150))           # 응답 index·배치 순서대로 제자리
    sizes.clear()
    await embeddings.embed(texts[:3])
    assert sizes == []                                               # 모두 캐시에서
    await http.aclose()
