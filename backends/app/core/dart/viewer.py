"""DART 웹 뷰어(dart.fss.or.kr) 본문. OpenDART API에 없는 잠정실적 표·부문 실적은 공시 원문에서 읽는다.

공시 원문은 접수번호가 같으면 바뀌지 않으므로(정정은 새 접수번호) 만료 없이 캐시한다.
"""
from __future__ import annotations

import asyncio
import html as htmllib
import logging
import re
from dataclasses import dataclass

import httpx

from app.core.cache import get_cache

log = logging.getLogger(__name__)
BASE = "https://dart.fss.or.kr"
_NODE = re.compile(r"node\d\['text'\]\s*=\s*\"([^\"]*)\";(.*?)cnt\+\+", re.S)
_PARAM = lambda name, block: (re.search(rf"\['{name}'\]\s*=\s*\"([^\"]*)\"", block) or [None, ""])[1]
_VIEWDOC = re.compile(r'viewDoc\("(\d+)",\s*"(\d+)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)"')


@dataclass
class Node:
    title: str
    url: str


def decode(b: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "ignore")


def to_text(page: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", htmllib.unescape(s)).strip()


def to_cells(page: str) -> list[str]:
    """표 셀·문단 경계를 살린 토큰 목록(빈 칸 제외)."""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", "\x00", s)
    toks = [re.sub(r"\s+", " ", htmllib.unescape(t)).strip() for t in s.split("\x00")]
    return [t for t in toks if t]


async def _get(http: httpx.AsyncClient, url: str) -> bytes:
    last: Exception | None = None
    for i in range(3):
        try:
            r = await http.get(url, headers={"User-Agent": "Mozilla/5.0 Finsight"}, timeout=30)
            r.raise_for_status()
            if len(r.content) > 200:
                return r.content
            last = RuntimeError(f"짧은 응답 {len(r.content)}B")
        except Exception as e:  # noqa: BLE001
            last = e
        await asyncio.sleep(1.5 * (i + 1))
    raise RuntimeError(f"DART 뷰어 요청 실패: {url}: {last}")


async def fetch(http: httpx.AsyncClient, url: str) -> str:
    cache = get_cache()
    hit = await cache.get("dart.view", url)
    if hit is not None:
        return hit
    page = decode(await _get(http, url))
    await cache.set("dart.view", url, page)
    return page


async def nodes(http: httpx.AsyncClient, rcept_no: str) -> list[Node]:
    """목차 노드. 목차가 없는 단일 문서(공정공시 등)는 문서 하나를 돌려준다."""
    main = await fetch(http, f"{BASE}/dsaf001/main.do?rcpNo={rcept_no}")
    out = []
    for title, block in _NODE.findall(main):
        p = {k: _PARAM(k, block) for k in ("rcpNo", "dcmNo", "eleId", "offset", "length", "dtd")}
        if p["dcmNo"]:
            out.append(Node(title.strip(), f"{BASE}/report/viewer.do?rcpNo={p['rcpNo']}&dcmNo={p['dcmNo']}"
                            f"&eleId={p['eleId']}&offset={p['offset']}&length={p['length']}&dtd={p['dtd']}"))
    if not out:
        m = _VIEWDOC.search(main)
        if m:
            rcp, dcm, ele, off, ln, dtd = m.groups()
            out.append(Node("본문", f"{BASE}/report/viewer.do?rcpNo={rcp}&dcmNo={dcm}&eleId={ele}&offset={off}"
                            f"&length={ln}&dtd={dtd}"))
    return out


async def section(http: httpx.AsyncClient, rcept_no: str, title_suffix: str) -> str | None:
    """목차 제목이 title_suffix로 끝나는 첫 절의 HTML."""
    for n in await nodes(http, rcept_no):
        if n.title.replace(" ", "").endswith(title_suffix.replace(" ", "")):
            return await fetch(http, n.url)
    return None


async def document(http: httpx.AsyncClient, rcept_no: str) -> str | None:
    ns = await nodes(http, rcept_no)
    return await fetch(http, ns[0].url) if ns else None
