"""OpenDART HTTP 클라이언트. 상태 코드 처리와 재시도만 맡는다(캐시는 호출하는 모듈이 키를 정해 쓴다).

상태 코드: 000 정상, 013 데이터 없음, 020 요청 한도 초과(재시도하지 않음), 800 시스템 점검(재시도).
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
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                log.warning("OpenDART %s 재시도 %d: %s", path, attempt + 1, e)
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
