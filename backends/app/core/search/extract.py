"""기사 본문 보충 추출(trafilatura). Tavily raw_content가 200자 미만이거나 발행일이 없을 때만 쓴다.

TLS 검증, 응답 본문 5MB 상한, 리다이렉트 최대 5회, 타임아웃 10초. 결과는 URL로 7일 캐시한다.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from app.core.cache import get_cache

log = logging.getLogger(__name__)
MAX_BYTES = 5 * 1024 * 1024
WEEK = 7 * 86_400
_sem = asyncio.Semaphore(8)
UA = "Mozilla/5.0 (compatible; FinsightBot/2.0)"


async def _download(http: httpx.AsyncClient, url: str) -> str | None:
    async with _sem:
        async with http.stream("GET", url, timeout=10, follow_redirects=True,
                               headers={"User-Agent": UA}) as r:
            if r.status_code >= 400:
                return None
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    log.info("본문 5MB 초과로 중단: %s", url)
                    break
            return bytes(buf).decode(r.encoding or "utf-8", errors="replace")


async def fetch_article(url: str, http: httpx.AsyncClient) -> dict:
    """{"text": str|None, "date": str|None}"""
    cache = get_cache()
    hit = await cache.get("article", url)
    if hit is not None:
        return hit
    out: dict = {"text": None, "date": None}
    try:
        html = await _download(http, url)
        if html:
            import trafilatura

            res = await asyncio.to_thread(trafilatura.extract, html, output_format="json", with_metadata=True,
                                          url=url, favor_precision=True)
            if res:
                d = json.loads(res)
                text = (d.get("text") or "").strip()
                out = {"text": text if len(text) >= 200 else None, "date": d.get("date")}
    except (httpx.HTTPError, UnicodeError) as e:
        log.info("본문 수집 실패 %s: %s", url, e)
    await cache.set("article", url, out, ttl_s=WEEK)
    return out
