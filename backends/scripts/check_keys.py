"""외부 API 키와 로컬 DB를 소량 요청으로 확인한다. 키 값과 키가 든 URL은 출력하지 않는다.

사용: cd backends && PYTHONPATH=. python scripts/check_keys.py
비용: Claude 16토큰 1회와 임베딩 1건(합계 0.0001달러 미만). Tavily는 크레딧을 쓰지 않는 /usage만 부른다.
"""
from __future__ import annotations

import asyncio
import re
import sys
import time

import httpx

from app.core import budget
from app.settings import get_settings

s = get_settings()
rows: list[tuple[str, bool | None, str]] = []


def sec(v) -> str | None:
    return v.get_secret_value() if v else None


def add(name: str, ok: bool | None, detail: str) -> None:
    rows.append((name, ok, detail))


async def check_anthropic() -> None:
    if not s.anthropic_api_key:
        return add("Anthropic", False, "ANTHROPIC_API_KEY 없음")
    try:
        from anthropic import AsyncAnthropic

        c = AsyncAnthropic(api_key=sec(s.anthropic_api_key), base_url="https://api.anthropic.com")
        m = await c.messages.create(model=s.llm_model, max_tokens=16, thinking={"type": "disabled"},
                                    messages=[{"role": "user", "content": "Reply with exactly: OK"}])
        txt = "".join(b.text for b in m.content if b.type == "text").strip()
        cost = budget.claude_cost(m.model, m.usage.input_tokens, m.usage.output_tokens, 0, 0)
        await budget.record("anthropic", cost, model=m.model, input_tokens=m.usage.input_tokens,
                            output_tokens=m.usage.output_tokens, meta={"task": "keycheck"})
        add("Anthropic", True, f"{m.model} 응답 '{txt}', {cost:.6f}달러")
    except Exception as e:  # noqa: BLE001
        add("Anthropic", False, type(e).__name__)


async def check_openrouter(http: httpx.AsyncClient) -> None:
    if not s.openrouter_key:
        return add("OpenRouter", False, "OPENROUTER_KEY 없음")
    try:
        r = await http.get(f"{s.openrouter_base_url}/key", headers={"Authorization": f"Bearer {sec(s.openrouter_key)}"})
        d = r.json().get("data", {}) if r.status_code == 200 else {}
        from app.core.embeddings import embed

        v = await embed([f"키 확인 {time.time()}"], http=http)
        add("OpenRouter", r.status_code == 200 and v.shape[1] == 1024,
            f"HTTP {r.status_code}, 키 누적 사용 {d.get('usage')}달러, 임베딩 {v.shape[1]}차원")
    except Exception as e:  # noqa: BLE001
        add("OpenRouter", False, type(e).__name__)


async def check_opendart(http: httpx.AsyncClient) -> None:
    if not s.opendart_api_key:
        return add("OpenDART", False, "OPENDART_API_KEY 없음")
    try:
        r = await http.get("https://opendart.fss.or.kr/api/company.json",
                           params={"crtfc_key": sec(s.opendart_api_key), "corp_code": "00126380"})
        if r.status_code != 200:
            return add("OpenDART", False, f"HTTP {r.status_code}")
        j = r.json()
        add("OpenDART", j.get("status") == "000", f"status {j.get('status')} {j.get('message')}")
    except Exception as e:  # noqa: BLE001
        add("OpenDART", False, type(e).__name__)


async def check_tavily(http: httpx.AsyncClient) -> None:
    if not s.tavily_api_key:
        return add("Tavily", False, "TAVILY_API_KEY 없음")
    try:
        r = await http.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {sec(s.tavily_api_key)}"})
        j = r.json() if r.status_code == 200 else {}
        a = j.get("account", {})
        add("Tavily", r.status_code == 200,
            f"HTTP {r.status_code}, 플랜 {a.get('current_plan')} {a.get('plan_usage')}/{a.get('plan_limit')}크레딧")
    except Exception as e:  # noqa: BLE001
        add("Tavily", False, type(e).__name__)


async def check_law(http: httpx.AsyncClient) -> None:
    if not s.law_oc:
        return add("법령 API", False, "LAW_OC 없음")
    fails = 0
    for _ in range(6):  # 연결이 가끔 끊기므로 재시도한다
        try:
            r = await http.get("https://www.law.go.kr/DRF/lawSearch.do",
                               params={"OC": sec(s.law_oc), "target": "law", "type": "XML", "query": "전자금융거래법"})
            ok = "<resultCode>00</resultCode>" in r.text
            n = len(re.findall(r"<law id=", r.text))
            return add("법령 API", ok, f"검색 결과 {n}건, 연결 끊김 {fails}회")
        except Exception:  # noqa: BLE001
            fails += 1
            await asyncio.sleep(1.5)
    add("법령 API", False, f"연결 실패 {fails}회")


async def check_langsmith(http: httpx.AsyncClient) -> None:
    if not s.langsmith_api_key:
        return add("LangSmith", None, "키 없음(선택)")
    try:
        r = await http.get("https://api.smith.langchain.com/api/v1/sessions", params={"limit": 1},
                           headers={"x-api-key": sec(s.langsmith_api_key)})
        add("LangSmith", r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        add("LangSmith", False, type(e).__name__)


async def check_weaviate(http: httpx.AsyncClient) -> None:
    try:
        r = await http.get(f"{s.weaviate_url}/v1/meta")
        add("Weaviate(로컬)", r.status_code == 200, f"버전 {r.json().get('version')}" if r.status_code == 200
            else f"HTTP {r.status_code}")
    except Exception as e:  # noqa: BLE001
        add("Weaviate(로컬)", False, f"{type(e).__name__} (docker compose up -d weaviate)")


async def check_neo4j() -> None:
    try:
        from neo4j import AsyncGraphDatabase

        async with AsyncGraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, sec(s.neo4j_password) or "")) as d:
            await d.verify_connectivity()
        add("Neo4j(로컬)", True, s.neo4j_uri)
    except Exception as e:  # noqa: BLE001
        add("Neo4j(로컬)", False, f"{type(e).__name__} (docker compose up -d neo4j)")


async def main() -> int:
    async with httpx.AsyncClient(timeout=30) as http:
        await asyncio.gather(check_anthropic(), check_openrouter(http), check_opendart(http), check_tavily(http),
                             check_law(http), check_langsmith(http), check_weaviate(http), check_neo4j())
    order = ["Anthropic", "OpenRouter", "OpenDART", "Tavily", "법령 API", "LangSmith", "Weaviate(로컬)", "Neo4j(로컬)"]
    rows.sort(key=lambda r: order.index(r[0]) if r[0] in order else 99)
    for name, ok, detail in rows:
        mark = "OK  " if ok else ("-   " if ok is None else "FAIL")
        print(f"{mark} {name:14s} {detail}")
    return 0 if all(ok is not False for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
