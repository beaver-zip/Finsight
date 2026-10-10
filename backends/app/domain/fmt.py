"""리포트 v2 표기 규칙. 금액은 1조원 이상이면 조원(소수 첫째 자리), 그 아래는 억원(정수)."""
from __future__ import annotations

from datetime import date


def money(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "–"
    a = abs(x)
    if a >= 1e12:
        return f"{x / 1e12:,.{digits}f}조원"
    if a >= 1e8:
        return f"{x / 1e8:,.0f}억원"
    if a >= 1e4:
        return f"{x / 1e4:,.0f}만원"
    return f"{x:,.0f}원"


def money_abs(x: float | None, digits: int = 1) -> str:
    return money(abs(x), digits) if x is not None else "–"


def num(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{x:,.{d}f}"


def pct(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{x:.{d}f}%"


def signed(x: float | None, d: int = 1, unit: str = "") -> str:
    if x is None:
        return "–"
    if round(x, d) == 0:
        return f"{0:.{d}f}{unit}"
    return f"{'+' if x > 0 else ''}{x:,.{d}f}{unit}"


def spct(x: float | None, d: int = 1) -> str:
    return signed(x, d, "%")


def pp(x: float | None, d: int = 1) -> str:
    return signed(x, d, "%p")


def times(x: float | None, d: int = 1) -> str:
    return "–" if x is None else f"{x:,.{d}f}배"


def won(x: float | None) -> str:
    return "–" if x is None else f"{x:,.0f}원"


def change(x: float | None, d: int = 1) -> str:
    """증감률을 방향과 함께: '24.1% 증가'."""
    if x is None:
        return "–"
    return f"{abs(x):.{d}f}% {'증가' if x >= 0 else '감소'}"


def move(x: float | None, d: int = 1) -> str:
    """주가·배수 변화: '21.6% 하락'."""
    if x is None:
        return "–"
    return f"{abs(x):.{d}f}% {'상승' if x >= 0 else '하락'}"


def vs(x: float | None, d: int = 1) -> str:
    """기대치 대비: '1.3% 상회'."""
    if x is None:
        return "–"
    return f"{abs(x):.{d}f}% {'상회' if x >= 0 else '하회'}"


def amount_change(x: float | None) -> str:
    if x is None:
        return "–"
    return f"{money(abs(x))} {'증가' if x >= 0 else '감소'}"


def fraction(r: float | None) -> str:
    """비율을 '3분의 1'처럼(가까운 단순 분수가 없으면 '0.42배')."""
    if r is None:
        return "–"
    for den in (2, 3, 4, 5):
        for numr in range(1, den):
            if abs(r - numr / den) <= 0.02:
                return f"{den}분의 {numr}"
    return f"{r:.2f}배"


def ym(d: date | str) -> str:
    s = d.isoformat() if isinstance(d, date) else d
    return f"{s[:4]}년 {int(s[5:7])}월"


def md(d: date | str) -> str:
    s = d.isoformat() if isinstance(d, date) else d
    return f"{int(s[5:7])}월 {int(s[8:10])}일"


def tbl(x: float | None, div: float, d: int = 0) -> str:
    return "–" if x is None else f"{x / div:,.{d}f}"
