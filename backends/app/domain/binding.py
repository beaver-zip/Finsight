"""숫자 바인딩: 사실표 생성, {{슬롯}} 렌더링, 슬롯 밖 숫자 검사."""
from __future__ import annotations

import html
import re
from decimal import ROUND_HALF_UP, Decimal

from app.domain.models import Metric

SLOT = re.compile(r"\{\{\s*([A-Za-z0-9_]+\.[A-Za-z0-9]+)\s*\}\}")
NEWS_REF = re.compile(r"\[(n\d+)\]")
NUMBER = re.compile(r"\d[\d,.]*")
_DATE_SUFFIX = ("년", "월", "일", "분기", "반기")
_ARTICLE = re.compile(r"제\s*(\d+)\s*(?:조(?:의\s*(\d+))?|항|호)")
_ANNEX = re.compile(r"별표\s*(\d+)(?:\s*의\s*(\d+))?")


def _q(v: Decimal, places: str) -> Decimal:
    return v.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def display(m: Metric) -> str:
    """표시 단계 반올림: 비율(%) 소수 첫째 자리, 배수 소수 둘째 자리, 금액 억 원 단위 정수."""
    if m.value is None:
        return m.note or "—"
    if m.unit == "%":
        s = f"{_q(m.value, '0.1')}%"
    elif m.unit == "x":
        s = f"{_q(m.value, '0.01')}배"
    else:
        s = f"{int(_q(m.value / Decimal(100_000_000), '1')):,}억 원"
    return f"{s}({m.note})" if m.note else s


def facts_table(metrics: dict[str, Metric]) -> list[dict]:
    """결론 노드에 주는 사실표. 값이 있거나 note가 있는 지표만."""
    rows = []
    for m in metrics.values():
        if m.value is None and not m.note:
            continue
        rows.append({"id": m.slot_id, "label": f"{m.label}({m.period})", "display": display(m)})
    return rows


def slots_in(text: str) -> list[str]:
    return SLOT.findall(text)


def missing_slots(text: str, by_slot: dict[str, Metric]) -> list[str]:
    return [s for s in slots_in(text) if s not in by_slot]


def tooltip(m: Metric) -> str:
    parts = [f"{r.account_nm}({r.column}) rcpNo {r.rcept_no}" for r in m.inputs]
    return f"{m.formula} · " + " / ".join(parts) if parts else m.formula


def render(text: str, by_slot: dict[str, Metric], *, as_html: bool = False) -> str:
    """슬롯을 표시값(HTML이면 출처 툴팁 포함)으로 바꾼다. 없는 슬롯은 그대로 둔다(검증 노드가 잡는다)."""
    def sub(mt: re.Match) -> str:
        m = by_slot.get(mt.group(1))
        if m is None:
            return mt.group(0)
        if not as_html:
            return display(m)
        return f'<span class="metric" title="{html.escape(tooltip(m))}">{html.escape(display(m))}</span>'
    return SLOT.sub(sub, html.escape(text) if as_html else text)


def _strip_number(tok: str) -> str:
    return tok.rstrip(".,")


def untraceable_numbers(text: str, sources: list[str]) -> list[str]:
    """슬롯·기사 ID를 지운 문장에서 숫자를 찾아, 날짜·기간 표기, 조문 표기, 인용한 출처 본문에 있는 숫자가
    아니면 돌려준다."""
    body = NEWS_REF.sub(" ", SLOT.sub(" ", text))
    allowed_spans: list[tuple[int, int]] = []
    for rx in (_ARTICLE, _ANNEX):
        for mt in rx.finditer(body):
            allowed_spans.append(mt.span())
    joined = "\n".join(sources)
    bad = []
    for mt in NUMBER.finditer(body):
        tok = _strip_number(mt.group(0))
        if not tok:
            continue
        start, end = mt.start(), mt.start() + len(tok)
        if any(a <= start and end <= b for a, b in allowed_spans):
            continue
        rest = body[end:end + 2].lstrip()
        if rest.startswith(_DATE_SUFFIX):
            continue
        if tok in joined:
            continue
        bad.append(tok)
    return bad
