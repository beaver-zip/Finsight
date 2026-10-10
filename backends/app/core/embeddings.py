"""bge-m3 임베딩(OpenRouter). v1의 중복 구현 2개를 통합했다. 결과는 (모델, 텍스트 해시)로 영구 캐시한다."""
from __future__ import annotations

import asyncio

import httpx
import numpy as np

from app.core import budget
from app.core.cache import get_cache, make_key
from app.settings import get_settings, require

BATCH = 64
_sem = asyncio.Semaphore(4)


async def embed(texts: list[str], *, http: httpx.AsyncClient | None = None) -> np.ndarray:
    """정규화된 임베딩 행렬(len(texts) × 1024)."""
    s = get_settings()
    cache = get_cache()
    keys = [make_key(s.embedding_model, t) for t in texts]
    out: list[list[float] | None] = [await cache.get("emb", k) for k in keys]
    todo = [i for i, v in enumerate(out) if v is None]
    if todo:
        own = http is None
        http = http or httpx.AsyncClient(timeout=30)
        try:
            for start in range(0, len(todo), BATCH):
                idx = todo[start:start + BATCH]
                vecs = await _call(http, [texts[i] for i in idx])
                for i, v in zip(idx, vecs):
                    out[i] = v
                    await cache.set("emb", keys[i], v)
        finally:
            if own:
                await http.aclose()
    m = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.clip(norms, 1e-12, None)


async def _call(http: httpx.AsyncClient, batch: list[str]) -> list[list[float]]:
    s = get_settings()
    await budget.check_budget()
    async with _sem:
        r = await http.post(f"{s.openrouter_base_url}/embeddings",
                            headers={"Authorization": f"Bearer {require(s.openrouter_key, 'OPENROUTER_KEY')}"},
                            json={"model": s.embedding_model, "input": batch})
    r.raise_for_status()
    data = r.json()
    if "data" not in data:
        raise RuntimeError(f"임베딩 실패: {str(data)[:200]}")
    usage = data.get("usage") or {}
    cost = float(usage.get("cost") or 0.0)
    await budget.record("openrouter", cost, model=s.embedding_model,
                        input_tokens=int(usage.get("prompt_tokens") or 0), meta={"task": "embed", "n": len(batch)})
    budget.add_request_usage("openrouter", 0, cost)
    return [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]


def cosine_matrix(m: np.ndarray) -> np.ndarray:
    """정규화된 행렬의 코사인 유사도 행렬."""
    return m @ m.T
