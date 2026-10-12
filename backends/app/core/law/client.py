"""국가법령정보 Open API 클라이언트(lawSearch.do·lawService.do). 재시도와 키 가리기만 맡는다.

URL과 응답 본문(상세 링크)에 OC 키가 들어 있으므로 예외 메시지·로그에 URL을 넣지 않고, 돌려주는 XML에서 OC 값을 지운다.
연결이 가끔 끊겨 네트워크 오류와 5xx는 1·2·4·8초 간격으로 다시 시도한다.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.core.cache import get_cache, make_key
from app.settings import get_settings, require

log = logging.getLogger(__name__)
BASE = "https://www.law.go.kr/DRF"
RETRY_DELAYS = (1, 2, 4, 8)
_OC = re.compile(r"(OC=)[^&\"'<\s]*")


class LawApiError(RuntimeError):
    pass


def scrub(text: str) -> str:
    """응답·URL에서 OC 값을 지운다."""
    return _OC.sub(r"\1***", text)


class LawClient:
    def __init__(self, http: httpx.AsyncClient, *, concurrency: int = 2):
        self.http = http
        self._sem = asyncio.Semaphore(concurrency)
        self._oc = require(get_settings().law_oc, "LAW_OC")

    async def _get(self, path: str, params: dict[str, Any]) -> str:
        last: Exception | None = None
        for attempt, delay in enumerate((0, *RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with self._sem:
                    r = await self.http.get(f"{BASE}/{path}", params={"OC": self._oc, "type": "XML", **params},
                                            timeout=30)
                r.raise_for_status()
                return scrub(r.text)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                last = LawApiError(f"법령 API {path} HTTP {code}")
                log.warning("법령 API %s 실패 %d: HTTP %s", path, attempt + 1, code)
                if code < 500:
                    break
            except httpx.TransportError as e:
                last = LawApiError(f"법령 API {path} {type(e).__name__}")
                log.warning("법령 API %s 재시도 %d: %s", path, attempt + 1, type(e).__name__)
        assert last is not None
        raise last

    async def xml(self, path: str, *, ttl: int | None = 7 * 86400, **params: Any) -> str:
        """응답 XML(OC를 지운 것). 같은 요청은 ttl 동안 캐시한다."""
        cache = get_cache()
        key = make_key(path, sorted(params.items()))
        hit = await cache.get("law", key)
        if hit is not None:
            return hit
        text = await self._get(path, params)
        await cache.set("law", key, text, ttl_s=ttl)
        return text

    async def search(self, target: str, query: str, **params: Any) -> str:
        return await self.xml("lawSearch.do", target=target, query=query, display=params.pop("display", 20), **params)

    async def service(self, target: str, **params: Any) -> str:
        return await self.xml("lawService.do", target=target, **params)
