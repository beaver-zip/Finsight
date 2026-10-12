"""평가 judge: OpenRouter의 Claude 외 모델이 예·아니오를 판정한다(검증 노드가 Haiku라 같은 모델이 자기 결과를 채점하지 않게).

- 후보 3개 중 사람 라벨(legal_qa dev 30문항)과 Cohen's κ가 가장 높은 모델을 고른다(eval/calibrate_judge.py).
  고르기 전에는 LLM_MODEL_JUDGE가 비어 있으면 PROVISIONAL을 쓰고 결과에 "임시 judge"를 적는다.
- 응답은 JSON 한 개로 받는다. 파싱에 실패하면 1회 다시 묻고, 그래도 실패하면 None(판정 불가)을 돌려준다.
- 판정 결과는 (모델, 프롬프트) 키로 영구 캐시한다. 재실행하면 같은 판정을 낸다.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core import budget
from app.core.cache import get_cache, make_key
from app.settings import get_settings, require

CANDIDATES = ["google/gemini-2.5-flash", "openai/gpt-oss-120b", "deepseek/deepseek-v4-flash"]
PROVISIONAL = "google/gemini-2.5-flash"


def judge_model() -> tuple[str, bool]:
    """(모델, 임시 여부)."""
    m = get_settings().llm_model_judge
    return (m, False) if m else (PROVISIONAL, True)


def _json(text: str) -> Any:
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else None


async def ask(prompt: str, *, model: str | None = None, system: str | None = None, task: str = "eval.judge",
              http: httpx.AsyncClient | None = None) -> Any:
    model = model or judge_model()[0]
    s = get_settings()
    cache = get_cache()
    key = make_key("judge", model, system, prompt)
    hit = await cache.get("judge", key)
    if hit is not None:
        return hit
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    own = http is None
    http = http or httpx.AsyncClient(timeout=120)
    try:
        for attempt in range(2):
            await budget.check_budget()
            r = await http.post(f"{s.openrouter_base_url}/chat/completions",
                                headers={"Authorization": f"Bearer {require(s.openrouter_key, 'OPENROUTER_KEY')}"},
                                json={"model": model, "messages": msgs, "temperature": 0, "usage": {"include": True},
                                      "response_format": {"type": "json_object"}})
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage") or {}
            await budget.record("openrouter", float(usage.get("cost") or 0.0), model=model,
                                input_tokens=int(usage.get("prompt_tokens") or 0),
                                output_tokens=int(usage.get("completion_tokens") or 0), meta={"task": task})
            try:
                out = _json(data["choices"][0]["message"]["content"] or "")
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                out = None
            if out is not None:
                await cache.set("judge", key, out)
                return out
            msgs.append({"role": "user", "content": "JSON 객체 하나만 다시 답하라."})
    finally:
        if own:
            await http.aclose()
    return None


FACTS_SYSTEM = """너는 법령 답변 채점자다. 답변이 핵심 사실 각각을 담고 있는지 판정한다.
표현이 달라도 뜻이 같으면 예, 빠졌거나 틀리면 아니오다. JSON으로만 답한다: {"facts": [true, false, ...]}(핵심 사실 순서대로)."""

SUPPORT_SYSTEM = """너는 인용 검증자다. 문장마다 함께 준 인용 원문이 그 문장을 뒷받침하는지 판정한다.
원문에 없는 내용을 더했거나 원문과 다르면 아니오다. JSON으로만 답한다: {"supported": [true, false, ...]}(문장 순서대로)."""

CHECK_SYSTEM = """너는 챗봇 답변 채점자다. 체크리스트 항목마다 답변이 그 항목을 충족하는지 예·아니오로 판정한다.
JSON으로만 답한다: {"items": [true, false, ...]}(항목 순서대로)."""

SAME_SYSTEM = """두 질문이 같은 것을 묻는지(대상·조건·요청이 같은지) 판정한다. 표현 차이는 무시한다.
JSON으로만 답한다: {"same": true} 또는 {"same": false}."""


async def key_facts(answer: str, facts: list[str], **kw) -> list[bool] | None:
    if not facts:
        return []
    out = await ask(f"답변:\n{answer}\n\n핵심 사실:\n" + "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts)),
                    system=FACTS_SYSTEM, task="eval.judge.facts", **kw)
    v = (out or {}).get("facts") if isinstance(out, dict) else None
    return [bool(x) for x in v] if isinstance(v, list) and len(v) == len(facts) else None


async def supported(pairs: list[tuple[str, str]], **kw) -> list[bool] | None:
    if not pairs:
        return []
    body = "\n\n".join(f"{i + 1}. 문장: {s}\n   인용 원문: {c[:1500]}" for i, (s, c) in enumerate(pairs))
    out = await ask(body, system=SUPPORT_SYSTEM, task="eval.judge.support", **kw)
    v = (out or {}).get("supported") if isinstance(out, dict) else None
    return [bool(x) for x in v] if isinstance(v, list) and len(v) == len(pairs) else None


async def checklist(answer: str, items: list[str], **kw) -> list[bool] | None:
    out = await ask(f"답변:\n{answer}\n\n체크리스트:\n" + "\n".join(f"{i + 1}. {x}" for i, x in enumerate(items)),
                    system=CHECK_SYSTEM, task="eval.judge.checklist", **kw)
    v = (out or {}).get("items") if isinstance(out, dict) else None
    return [bool(x) for x in v] if isinstance(v, list) and len(v) == len(items) else None


async def same_question(a: str, b: str, **kw) -> bool | None:
    out = await ask(f"질문 A: {a}\n질문 B: {b}", system=SAME_SYSTEM, task="eval.judge.same", **kw)
    return bool(out.get("same")) if isinstance(out, dict) and "same" in out else None
