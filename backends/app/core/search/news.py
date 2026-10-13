"""뉴스 검색(Tavily).

설계서 미확정 사양 "Tavily의 한국어 뉴스 수" 확인 결과(2026-10-10, 8개 기업 × 첫 묶음 질의):
topic="news"는 질의당 평균 9.8건, 제목에 한글이 든 기사 평균 8.1건(기준 10건 미만)이고 발행일은 절반만 왔다.
기준대로 topic="general" + country="south korea" + include_domains(SOURCE_TIER 1등급) +
include_published_date=true로 바꿨다(같은 질의 3개에서 20건 중 한글 19건, 발행일 15–18건).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from app.core.search import tavily
from app.domain.dates import parse_published
from app.domain.sources import TIER1, domain_of

NEWS_MODE = "general_domains"   # "news"로 바꾸면 topic="news"


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    raw_content: str | None
    published_at: datetime | None
    published_raw: str | None
    source: str
    rank: int


async def search_news(query: str, *, time_range: tavily.TimeRange = "month", max_results: int = 20,
                      http: httpx.AsyncClient | None = None) -> list[SearchHit]:
    if NEWS_MODE == "news":
        data = await tavily.search(query, topic="news", time_range=time_range, max_results=max_results, http=http)
    else:
        data = await tavily.search(query, topic="general", country="south korea", include_domains=list(TIER1),
                                   include_published_date=True, time_range=time_range, max_results=max_results,
                                   http=http)
    return _hits(data)


def _hits(data: dict) -> list[SearchHit]:
    out = []
    for i, r in enumerate(data.get("results", [])):
        raw = (r.get("raw_content") or "").strip() or None
        out.append(SearchHit(title=(r.get("title") or "").strip(), url=r.get("url") or "",
                             snippet=(r.get("content") or "").strip(), raw_content=raw,
                             published_at=parse_published(r.get("published_date")),
                             published_raw=r.get("published_date"), source=domain_of(r.get("url") or ""), rank=i))
    return out
