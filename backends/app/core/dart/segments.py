"""부문별 매출·영업이익(누적). OpenDART 재무제표 API에는 부문 정보가 없어 정기보고서 'II. 사업의 내용'의
'사업부문별 요약 재무 현황' 같은 표에서 읽는다. 표 형식이 회사마다 달라 못 찾으면 None(차트·표에서 뺀다).
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

import httpx

from app.core.dart import viewer
from app.core.dart.prelim import UNITS, _num

log = logging.getLogger(__name__)
HEADINGS = ("부문별요약재무", "부문별재무현황", "부문별요약재무현황", "사업부문별재무정보", "부문별재무정보")
_NUMTOK = re.compile(r"^[△\-]?[\d,]+(?:\.\d+)?%?$")
_STOP = {"비중", "금액", "구분", "구 분", "부문", "부 문"}


def parse_segments(text: str) -> tuple[dict[str, dict[str, Decimal]], str] | None:
    flat = text.replace(" ", "")
    pos = -1
    for h in HEADINGS:
        i = flat.find(h)
        if i >= 0:
            pos = i
            break
    if pos < 0:
        return None
    # 공백을 지운 위치를 원문 위치로 옮긴다
    cnt, start = 0, 0
    for idx, ch in enumerate(text):
        if ch != " ":
            if cnt == pos:
                start = idx
                break
            cnt += 1
    body = text[start:]
    mu = re.search(r"단위\s*:\s*(조원|억원|백만원|천원|원)", body[:600])
    unit = mu.group(1) if mu else "억원"
    k = UNITS[unit]
    end = body.find("※", 40)
    body = body[: end if end > 0 else 6000]
    parts = re.split(r"매출액", body)
    out: dict[str, dict[str, Decimal]] = {}
    for i in range(1, len(parts)):
        before = parts[i - 1].split()
        name_toks = []
        for t in reversed(before):
            if _NUMTOK.match(t) or t in ("비중", "금액", "총자산", "자산총계"):
                break
            name_toks.append(t)
        name = " ".join(reversed(name_toks)).strip()
        name = re.sub(r"^(부\s?문|구\s?분)\s*", "", name).strip()
        if not name or len(name) > 20:
            continue
        seg = parts[i]
        m_rev = re.search(r"([△\-]?[\d,]{2,})", seg)
        m_op = re.search(r"영업이익\s+([△\-]?[\d,]+)", seg)
        if not m_rev or not m_op:
            continue
        rev, op = _num(m_rev.group(1)), _num(m_op.group(1))
        if rev is None or op is None:
            continue
        out[name] = {"rev": rev * k, "op": op * k}
    return (out, unit) if len(out) >= 2 else None


async def segment_cumulative(http: httpx.AsyncClient, rcept_no: str) -> dict[str, dict[str, Decimal]] | None:
    """정기보고서 하나의 누적 부문 실적. 'II. 사업의 내용' 하위 절을 차례로 보고 처음 읽히는 표를 쓴다."""
    try:
        ns = await viewer.nodes(http, rcept_no)
    except Exception as e:  # noqa: BLE001
        log.info("부문 목차 실패 %s: %s", rcept_no, e)
        return None
    in_business, cands = False, []
    for n in ns:
        t = n.title.replace(" ", "")
        if t.startswith("II.") or t.startswith("Ⅱ."):
            in_business = True
            continue
        if in_business and (t.startswith("III.") or t.startswith("Ⅲ.")):
            break
        if in_business:
            cands.append(n)
    # '기타 참고사항'에 있는 경우가 많아 먼저 본다
    cands.sort(key=lambda n: 0 if "기타참고사항" in n.title.replace(" ", "") else 1)
    for n in cands:
        try:
            page = await viewer.fetch(http, n.url)
        except Exception as e:  # noqa: BLE001
            log.info("부문 절 실패 %s: %s", n.title, e)
            continue
        got = parse_segments(viewer.to_text(page))
        if got:
            return got[0]
    return None
