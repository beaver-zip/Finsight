"""부문별 매출·영업이익(누적). OpenDART 재무제표 API에는 부문 정보가 없어 정기보고서 원문에서 읽는다.

1. 'II. 사업의 내용'의 '사업부문별 요약 재무 현황' 같은 글 표(삼성전자 형식)
2. 연결재무제표 주석의 '영업부문에 대한 공시' 표(XBRL 형식이라 회사가 달라도 모양이 비슷하다)

둘 다 못 읽으면 None(차트·표에서 뺀다).
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

import httpx
import lxml.html

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
        name = re.sub(r"\s*\(\*[\d,\s]*\)", "", name).strip()
        if not name or len(name) > 20 or re.search(r"\d|합계|총계|^계$", name):
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


# --- 연결재무제표 주석 '영업부문에 대한 공시' 표 ---

_PERIOD = re.compile(r"(당|전)\s*(기|분기|반기|\d\s*분기)")
_UNIT = re.compile(r"단위\s*:\s*(조원|억원|백만원|천원|원)")
# 열 머리말에서 부문 이름이 아닌 층(XBRL 차원 이름)
_GENERIC = {"부문", "영업부문", "보고부문", "부문의합계", "기업전체", "기업전체총계", "3개월", "누적", "당기", "당분기",
            "당반기", "금액"}
_DROP_COL = re.compile(r"합계|총계|소계|^계$|조정|제거|연결|내부|외부|비율|비중")
_REV = (re.compile(r"^총(부문)?(매출액?|수익|영업수익)"),
        re.compile(r"^(부문)?(매출액?|영업수익|수익|매출수익)(\(.*\))?$"),
        re.compile(r"^외부(고객으로부터의)?(매출액?|수익)"))
_OP = (re.compile(r"^(보고)?(부문)?영업(이익|손익|손실)"),
       re.compile(r"^(보고)?부문(이익|손익)"))


def _amount(tok: str) -> Decimal | None:
    t = tok.strip()
    neg = t.startswith("(") and t.endswith(")")
    v = _num(t.strip("()")) if _NUMTOK.match(t.strip("()")) else None
    return -v if (v is not None and neg) else v


def _key(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _pick_row(rows: list[tuple[str, list[str]]], pats) -> list[str] | None:
    for pat in pats:
        for label, cells in rows:
            k = _key(label)
            if pat.search(k) and not re.search(r"세전|순이익|내부|부문간|정부|이자", k) \
                    and any(_amount(x) is not None for x in cells):
                return cells
    return None


def _seg_name(path: list[str]) -> str | None:
    """열 머리말 경로에서 부문 이름. 합계·조정·내부거래 열이면 None."""
    keys = [_key(x) for x in path]
    if any(_DROP_COL.search(k) for k in keys if k not in _GENERIC):
        return None
    if any(re.search(r"전(기|분기|반기)", k) for k in keys):
        return None
    names = [x for x, k in zip(path, keys) if k and k not in _GENERIC]
    if not names:
        return None
    name = re.sub(r"\s*\(\*[\d,\s]*\)\s*", "", names[-1]).strip()
    return name or None


def parse_note_table(g: list[list[str]], k: Decimal) -> dict[str, dict[str, Decimal]] | None:
    """부문이 열인 표(머리말 여러 층, 행이 매출·영업이익)에서 부문별 누적 매출·영업이익."""
    if not g:
        return None
    width = max(len(r) for r in g)
    g = [r + [""] * (width - len(r)) for r in g]
    nh = 0
    while nh < len(g) and not g[nh][0]:
        nh += 1
    if nh == 0 or nh == len(g):
        return None
    cols = [j for j in range(1, width) if any(g[i][j] for i in range(nh))]
    paths = {j: [g[i][j] for i in range(nh)] for j in cols}
    if any("누적" in p for p in paths.values()):      # 3개월·누적이 같이 있으면 누적만
        cols = [j for j in cols if "3개월" not in paths[j]]
    body = [(r[0], r) for r in g[nh:]]
    rev, op = _pick_row(body, _REV), _pick_row(body, _OP)
    if rev is None or op is None:
        return None
    out: dict[str, dict[str, Decimal]] = {}
    for j in cols:
        name = _seg_name(paths[j])
        rv, ov = _amount(rev[j]), _amount(op[j])
        if name and name not in out and rv is not None and ov is not None:
            out[name] = {"rev": rv * k, "op": ov * k}
    return out if len(out) >= 2 else None


def parse_notes(page: str) -> tuple[dict[str, dict[str, Decimal]], str] | None:
    """연결재무제표 주석에서 당기(누적) '영업부문에 대한 공시' 표를 찾아 읽는다."""
    try:
        doc = lxml.html.fromstring(page)
    except Exception:  # noqa: BLE001
        return None
    title, ctx, unit = "", "", None
    for t in doc.xpath("//table"):
        txt = re.sub(r"\s+", " ", t.text_content()).strip()
        if t.get("border") != "1":          # 표 제목·기간·단위 줄(테두리 없는 표)
            if "공시" in txt or "부문" in txt:
                title = txt
            ctx += " " + txt
            if (m := _UNIT.search(txt)):
                unit = m.group(1)
            continue
        head, ctx = ctx, ""
        if "부문" not in title or "지역" in title or not unit:
            continue
        pers = _PERIOD.findall(head) or _PERIOD.findall(title)    # 표 바로 위의 기간 표시
        if pers and pers[-1][0] == "전":
            continue
        got = parse_note_table(viewer.grid(t), UNITS[unit])
        if got:
            return got, unit
    return None


async def _business(http: httpx.AsyncClient, ns: list) -> dict[str, dict[str, Decimal]] | None:
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


async def _notes(http: httpx.AsyncClient, ns: list) -> dict[str, dict[str, Decimal]] | None:
    notes = [n for n in ns if "주석" in n.title and "연결" in n.title]
    for n in notes[:1]:
        try:
            page = await viewer.fetch(http, n.url)
        except Exception as e:  # noqa: BLE001
            log.info("부문 주석 실패 %s: %s", n.title, e)
            return None
        got = parse_notes(page)
        if got:
            return got[0]
    return None


SOURCES = {"business": "정기보고서 사업부문별 요약 재무 현황(내부거래 포함)",
           "notes": "정기보고서 연결재무제표 주석 영업부문 정보"}


async def segment_cumulative(http: httpx.AsyncClient, rcept_no: str, method: str | None = None
                             ) -> tuple[dict[str, dict[str, Decimal]], str] | None:
    """정기보고서 하나의 누적 부문 실적과 읽은 곳('notes'·'business'). 주석 표가 형식이 일정해 먼저 본다.

    method를 주면 그 방식만 쓴다(기간마다 읽는 곳이 달라 부문 이름이 어긋나지 않게 최신 보고서의 방식을 따른다).
    """
    try:
        ns = await viewer.nodes(http, rcept_no)
    except Exception as e:  # noqa: BLE001
        log.info("부문 목차 실패 %s: %s", rcept_no, e)
        return None
    for m in ([method] if method else ["notes", "business"]):
        got = await (_business(http, ns) if m == "business" else _notes(http, ns))
        if got:
            return got, m
    return None
