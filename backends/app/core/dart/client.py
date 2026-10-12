"""OpenDART HTTP 클라이언트. 상태 코드 처리와 재시도만 맡는다(캐시는 호출하는 모듈이 키를 정해 쓴다).

상태 코드: 000 정상, 013 데이터 없음, 020 요청 한도 초과(재시도하지 않음), 800 시스템 점검(재시도).
HTTP 5xx·네트워크 오류는 재시도하고, 3xx·4xx(점검·접속 제한 페이지로 돌려보내는 302 등)는 바로 실패한다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.settings import get_settings, require

log = logging.getLogger(__name__)
BASE = "https://opendart.fss.or.kr/api"
RETRY_DELAYS = (1, 2, 4)


class DartError(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(f"OpenDART {status}: {message}")
        self.status = status


class DartNoData(DartError):
    pass


class DartLimit(DartError):
    pass


class DartClient:
    def __init__(self, http: httpx.AsyncClient, *, concurrency: int = 4):
        self.http = http
        self._sem = asyncio.Semaphore(concurrency)
        self._key = require(get_settings().opendart_api_key, "OPENDART_API_KEY")

    async def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        last: Exception | None = None
        for attempt, delay in enumerate((0, *RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                async with self._sem:
                    r = await self.http.get(f"{BASE}/{path}", params={"crtfc_key": self._key, **params},
                                            timeout=10)
                r.raise_for_status()
                status = _status_of(path, r)
                if status == "800":
                    last = DartError("800", "시스템 점검")
                    continue
                return r
            except httpx.HTTPStatusError as e:
                # 예외 메시지에는 인증키가 든 URL이 있으므로 그대로 올리거나 기록하지 않는다
                code = e.response.status_code
                to = e.response.headers.get("location", "")
                last = DartError(str(code), f"HTTP {code}{' → ' + to if to else ''} ({path})")
                log.warning("OpenDART %s 실패 %d: HTTP %s %s", path, attempt + 1, code, to)
                if code < 500:          # 3xx·4xx(점검·접속 제한 페이지 등)는 다시 불러도 같다
                    break
            except httpx.TransportError as e:
                last = DartError("net", f"{type(e).__name__} ({path})")
                log.warning("OpenDART %s 재시도 %d: %s", path, attempt + 1, type(e).__name__)
        assert last is not None
        raise last

    async def json(self, endpoint: str, **params: Any) -> dict:
        r = await self._get(f"{endpoint}.json", params)
        data = r.json()
        status, msg = data.get("status"), data.get("message", "")
        if status == "000":
            return data
        if status == "013":
            raise DartNoData(status, msg)
        if status == "020":
            raise DartLimit(status, msg)
        raise DartError(status or "?", msg)

    async def binary(self, endpoint: str, **params: Any) -> bytes:
        """zip 같은 파일 응답. 오류는 XML(<result><status>…)로 온다."""
        r = await self._get(endpoint, params)
        status = _status_of(endpoint, r)
        if status is not None and status != "000":
            raise DartLimit(status, "요청 한도 초과") if status == "020" else DartError(status, _xml_message(r))
        return r.content


async def cached(namespace: str, key: str, ttl_s: float | None, fetch):
    """캐시 → 없거나 만료면 fetch. OpenDART가 실패하면(점검·접속 제한·한도) 만료된 값이라도 있으면 쓴다.
    목록·기업개황처럼 자주 바뀌지 않는 응답에만 쓴다."""
    from app.core.cache import get_cache

    cache = get_cache()
    hit = await cache.peek(namespace, key)
    if hit is not None and not hit[1]:
        return hit[0]
    try:
        value = await fetch()
    except DartError as e:
        if hit is not None:
            log.warning("OpenDART 실패(%s), 만료된 캐시를 쓴다: %s", e, namespace)
            return hit[0]
        raise
    await cache.set(namespace, key, value, ttl_s=ttl_s)
    return value


def _status_of(path: str, r: httpx.Response) -> str | None:
    if path.endswith(".json"):
        return r.json().get("status")
    if r.content[:5] == b"<?xml" and b"<result>" in r.content[:200]:
        import re
        m = re.search(rb"<status>(\d+)</status>", r.content)
        return m.group(1).decode() if m else None
    return None


def _xml_message(r: httpx.Response) -> str:
    import re
    m = re.search(r"<message>(.*?)</message>", r.text)
    return m.group(1) if m else ""
