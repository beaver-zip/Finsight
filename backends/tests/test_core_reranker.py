"""리랭커 예열: 락 안에서 한 번 올리고, 실패해도 서버를 멈추지 않는다."""
from __future__ import annotations

from app.core import reranker


async def test_warmup_loads_model_under_lock_and_swallows_errors(monkeypatch):
    seen = []
    monkeypatch.setattr(reranker, "_model", lambda: seen.append(reranker._lock.locked()))
    await reranker.warmup()
    assert seen == [True]                       # 요청의 rerank와 겹쳐도 모델을 두 번 올리지 않는다

    def boom():
        raise OSError("모델 파일 없음")
    monkeypatch.setattr(reranker, "_model", boom)
    await reranker.warmup()                     # 예외를 올리지 않는다(첫 rerank가 다시 시도한다)
    assert not reranker._lock.locked()
