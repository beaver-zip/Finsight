"""cited(): 긴 출력은 스트리밍으로 받고, max_tokens에서 잘린 답은 캐시하지 않는다."""
from __future__ import annotations

from anthropic.types import Message

from app.core import llm
from app.settings import get_settings


def _msg(stop: str) -> Message:
    return Message.model_validate({"id": "m", "type": "message", "role": "assistant", "model": "x",
                                   "content": [{"type": "text", "text": "답"}], "stop_reason": stop,
                                   "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})


class FakeClient:
    def __init__(self, stop: str):
        self.calls: list[str] = []
        self.resp = _msg(stop)
        self.messages = self

    async def create(self, **kw):
        self.calls.append("create")
        return self.resp

    def stream(self, **kw):
        self.calls.append("stream")
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        return self.resp


async def test_cited_streams_long_output_and_skips_caching_truncated(monkeypatch, temp_cache):
    monkeypatch.setattr(get_settings(), "llm_cache", True)
    fake = FakeClient("max_tokens")
    monkeypatch.setattr(llm, "client", lambda: fake)
    await llm.cited("질문", [], task="t")                         # 기본 16000토큰: 120초 읽기 제한을 피해 스트리밍
    assert fake.calls == ["stream"]
    assert temp_cache._run("SELECT count(*) FROM kv WHERE namespace='llm'") == [(0,)]   # 잘린 답은 캐시하지 않는다

    fake = FakeClient("end_turn")
    monkeypatch.setattr(llm, "client", lambda: fake)
    await llm.cited("질문", [], max_tokens=4_000, task="t")
    assert fake.calls == ["create"]
    assert temp_cache._run("SELECT count(*) FROM kv WHERE namespace='llm'") == [(1,)]
