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
GROWTH_IDS = {"revenue_growth", "op_income_growth", "net_income_growth"}
_ARTICLE = re.compile(r"제\s*(\d+)\s*(?:조(?:의\s*(\d+))?|항|호)")
_ANNEX = re.compile(r"별표\s*(\d+)(?:\s*의\s*(\d+))?")
# 기간 표기: 3Q26, 3Q26P, 2026H1, H126, FY25, 1~3분기, 3~5년
_PERIOD = re.compile(r"(?<![0-9A-Za-z])(?:[1-4]Q\d{2}P?|\d{4}H[12]|H[12]\d{2}|FY\d{2})(?![0-9])")
_RANGE = re.compile(r"\d+\s*[~∼～\-–]\s*\d+\s*(?=년|개월|월|일|분기|주|거래일)")


def _q(v: Decimal, places: str) -> Decimal:
    return v.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def display(m: Metric) -> str:
    """표시 단계 반올림: 비율(%) 소수 첫째 자리, 배수 소수 둘째 자리, 금액 억 원 단위 정수."""
    if m.text is not None:
        return m.text
    if m.value is None:
        return m.note or "—"
    if m.unit == "%" and m.id in GROWTH_IDS:
        v = _q(m.value, '0.1')
        s = f"{abs(v)}% {'증가' if v >= 0 else '감소'}"
    elif m.unit == "%":
        s = f"{_q(m.value, '0.1')}%"
    elif m.unit == "x":
        s = f"{_q(m.value, '0.01')}배"
    else:
        s = f"{int(_q(m.value / Decimal(100_000_000), '1')):,}억 원"
    return f"{s}({m.note})" if m.note else s


def slots_in(text: str) -> list[str]:
    return SLOT.findall(text)


def missing_slots(text: str, by_slot: dict[str, Metric]) -> list[str]:
    return [s for s in slots_in(text) if s not in by_slot]


# ---------- 슬롯 뒤 조사·방향어 다듬기 ----------
# 문장을 쓰는 모델은 슬롯 자리에 들어갈 글자를 모르므로 "55.1%으로", "35% 하락 하락함" 같은 문장이 나온다.
_DIGIT_JONG = {"0": (True, False), "1": (True, True), "2": (False, False), "3": (True, False), "4": (False, False),
               "5": (False, False), "6": (True, False), "7": (True, True), "8": (True, True), "9": (False, False)}
_DIR_END = re.compile(r"\s(증가|감소|상승|하락|상회|하회)$")
_DIR_VERB = re.compile(r"\s*(증가|감소|상승|하락|상회|하회|늘|줄|개선|악화|올라|오른|내려|내린|떨어|높아|낮아)")
_SIGN_VERB = re.compile(r"(?:으로|로)?\s*(증가|감소|상승|하락|상회|하회|늘|줄|개선|악화|올라|오른|내려|내린|떨어)")


def _jong(display_text: str) -> tuple[bool, bool]:
    """(받침 있음, 받침이 ㄹ). 숫자·기호는 읽는 소리로 본다(% 퍼센트, %p 포인트)."""
    t = re.sub(r"\([^)]*\)$", "", display_text.strip()).rstrip()
    if not t:
        return False, False
    ch = t[-1]
    if "가" <= ch <= "힣":
        j = (ord(ch) - 0xAC00) % 28
        return j != 0, j == 8
    if ch.isdigit():
        return _DIGIT_JONG[ch]
    return False, False                        # %, p, 배 외 기호·영문은 받침 없음으로 둔다


def josa(display_text: str, after: str) -> str:
    """after 앞머리의 조사를 display_text 끝소리에 맞춘다."""
    bat, rieul = _jong(display_text)
    for pair, pick in ((("으로", "로"), "로" if (not bat or rieul) else "으로"),
                       (("이었", "였"), "이었" if bat else "였"),
                       (("을", "를"), "을" if bat else "를"),
                       (("은", "는"), "은" if bat else "는"),
                       (("과", "와"), "과" if bat else "와")):
        for p in pair:
            if after.startswith(p):
                return pick + after[len(p):]
    m = re.match(r"(이|가)(?=[\s.,!?)\]]|$)", after)
    if m:
        return ("이" if bat else "가") + after[1:]
    return after


def tidy(display_text: str, after: str) -> tuple[str, str]:
    """표시값과 뒤 문장을 다듬는다: 방향어 겹침('35% 하락 하락함' → '35% 하락함'), 부호와 방향어 겹침
    ('+782.5% 증가' → '782.5% 증가'), 조사('55.1%으로' → '55.1%로')."""
    m = _DIR_END.search(display_text)
    if m and _DIR_VERB.match(after):
        display_text = display_text[:m.start()]
    elif m and re.match(r"\s*변화", after):          # '20.4% 증가 변화함' → '20.4% 증가함'
        after = re.sub(r"^\s*변화", "", after)
    elif re.match(r"^[+\-−]\d", display_text) and _SIGN_VERB.match(after):
        display_text = display_text[1:]
    elif re.match(r"^[\-−]\d", display_text) and re.match(r"\s*(?:의\s*)?(?:영업)?(?:적자|손실)", after):
        display_text = display_text[1:]                  # '-8,188억원 적자' → '8,188억원 적자'
    return display_text, josa(display_text, after)


def substitute(text: str, by_slot: dict[str, Metric], fmt) -> str:
    """슬롯을 fmt(metric, 표시 문자열)로 바꾸며 조사·방향어를 다듬는다. 없는 슬롯은 fmt(None, 원문)."""
    out, rest = [], text
    while True:
        m = SLOT.search(rest)
        if not m:
            out.append(rest)
            return "".join(out)
        out.append(rest[:m.start()])
        mt, after = by_slot.get(m.group(1)), rest[m.end():]
        if mt is None:
            out.append(fmt(None, m.group(0)))
            rest = after
            continue
        shown, rest = tidy(display(mt), after)
        out.append(fmt(mt, shown))


def tooltip(m: Metric) -> str:
    parts = [f"{r.account_nm}({r.column}) rcpNo {r.rcept_no}" for r in m.inputs]
    base = f"{m.formula} · " + " / ".join(parts) if parts else m.formula
    return f"{base} · {m.source}" if m.source else base


def render(text: str, by_slot: dict[str, Metric], *, as_html: bool = False) -> str:
    """슬롯을 표시값(HTML이면 출처 툴팁 포함)으로 바꾼다. 없는 슬롯은 그대로 둔다(검증 노드가 잡는다)."""
    def fmt(m: Metric | None, shown: str) -> str:
        if m is None or not as_html:
            return shown
        return f'<span class="metric" title="{html.escape(tooltip(m))}">{html.escape(shown)}</span>'
    return substitute(html.escape(text) if as_html else text, by_slot, fmt)


def _strip_number(tok: str) -> str:
    return tok.rstrip(".,")


def number_tokens(texts: list[str]) -> set[str]:
    return {_strip_number(t) for s in texts for t in NUMBER.findall(s or "")}


def untraceable_numbers(text: str, sources: list[str], tokens: set[str] | None = None) -> list[str]:
    """슬롯·기사 ID를 지운 문장에서 숫자를 찾아, 날짜·기간 표기, 조문 표기, 인용한 출처 본문에 있는 숫자,
    tokens(사실표·차트 제목에 보이는 숫자 토큰)가 아니면 돌려준다."""
    body = NEWS_REF.sub(" ", SLOT.sub(" ", text))
    allowed_spans: list[tuple[int, int]] = []
    for rx in (_ARTICLE, _ANNEX, _PERIOD, _RANGE):
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
        if tok in joined or (tokens and tok in tokens):
            continue
        bad.append(tok)
    return bad
