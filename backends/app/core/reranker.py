"""로컬 리랭커(bge-reranker-v2-m3, sentence-transformers CrossEncoder). 점수는 0–1(시그모이드).

모델은 처음 부를 때 한 번 올린다(약 2.3GB, ~/.cache/huggingface). GPU(MPS)가 있으면 쓰고, 호출은 스레드로 넘기고 지연을 기록한다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache

log = logging.getLogger(__name__)
MODEL = "BAAI/bge-reranker-v2-m3"
MAX_CHARS = 1500            # 512토큰을 넘는 부분은 어차피 잘린다


@lru_cache(maxsize=1)
def _model():
    import torch
    from sentence_transformers import CrossEncoder

    # M1 기준 후보 40개: CPU 21초, MPS 11초(2026-10-12 측정)
    return CrossEncoder(MODEL, max_length=512, device="mps" if torch.backends.mps.is_available() else "cpu")


def _score_sync(query: str, texts: list[str]) -> list[float]:
    import torch

    m = _model()
    with torch.inference_mode():
        out = m.predict([(query, t[:MAX_CHARS]) for t in texts], batch_size=16, show_progress_bar=False,
                        activation_fn=torch.nn.Sigmoid())
    return [float(x) for x in out]


_lock = asyncio.Lock()


async def rerank(query: str, texts: list[str]) -> list[float]:
    if not texts:
        return []
    t0 = time.perf_counter()
    async with _lock:                      # 모델 하나를 여러 요청이 동시에 쓰지 않게
        scores = await asyncio.to_thread(_score_sync, query, texts)
    log.info("리랭크 %d건 %.2f초", len(texts), time.perf_counter() - t0)
    return scores
