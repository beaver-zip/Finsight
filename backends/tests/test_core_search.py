"""검색 보조(기사 본문 수집)의 캐시 규칙."""
from __future__ import annotations

import httpx

from app.core.search import extract


async def test_article_transient_failure_is_not_cached(temp_cache):
    answers = {"https://a.kr/503": httpx.Response(503), "https://a.kr/429": httpx.Response(429),
               "https://a.kr/404": httpx.Response(404)}

    def handler(req):
        if req.url.path == "/timeout":
            raise httpx.ReadTimeout("slow", request=req)
        return answers[str(req.url)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        for url in ("https://a.kr/503", "https://a.kr/429", "https://a.kr/timeout", "https://a.kr/404"):
            assert await extract.fetch_article(url, http) == {"text": None, "date": None}
    for url in ("https://a.kr/503", "https://a.kr/429", "https://a.kr/timeout"):   # 일시 오류는 다음에 다시 받는다
        assert await temp_cache.get("article", url) is None
    assert await temp_cache.get("article", "https://a.kr/404") is not None       # 다시 받아도 같은 4xx는 7일 기억
