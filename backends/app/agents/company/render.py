"""리포트 v2 HTML: ReportData(차트·표·사실표) + ReportDraft(문장) → 스코프 CSS와 폰트를 내장한 HTML 조각.

디자인(목업 v9)
- 회색 체계 + 강조색 하나(초록). 빨강·파랑은 국내 관행대로 등락에만 쓴다.
- 차트는 BBC·Economist 문법: 제목은 주장, 부제에 단위, 범례는 위, 가로 격자 위에 눈금(맨 위 눈금에만 단위),
  이중축 없음, 강조 하나에 나머지는 회색. 데스크톱(760)·모바일(340) SVG를 따로 그려 글자 크기를 유지한다.
- 프론트(Report.tsx)가 innerHTML로 넣으므로 CSS는 .fs-report 아래로 한정하고, 폰트(Pretendard, SIL OFL)는
  문서에 쓰인 글자만 잘라 내장한다. OFL의 예약 글꼴 이름을 피하려고 내장 글꼴은 다른 family 이름을 쓴다.
"""
from __future__ import annotations

import base64
import html
import io
import logging
import math
import re
from datetime import date
from functools import lru_cache

from app.domain.binding import substitute, tooltip
from app.domain.models import Block, Headline, Metric, ReportDraft
from app.settings import BACKEND_DIR

log = logging.getLogger(__name__)
FONT_DIR = BACKEND_DIR / "assets" / "fonts"
FAMILY = "fsr-sans"

# ---------------- 토큰
INK, TXT, SUB, MUTED, AXIS = "#191F28", "#333D4B", "#4E5968", "#6B7684", "#8B95A1"
GRID, FAINT, G1, G2 = "#E5E8EB", "#F2F4F6", "#B0B8C1", "#D1D6DB"
ACC, TINT, WASH = "#0B7A5C", "#8FCBB8", "#EEF7F3"
UP, DN = "#D6343C", "#2E6BD8"
ROLE = {"acc": ACC, "mid": AXIS, "light": G2, "tint": TINT, "ink": INK}
SECTION_NAMES = {"earnings": "실적", "valuation": "밸류에이션", "cash": "현금흐름과 주주환원", "market": "주가와 뉴스"}


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def sgn(x: float, p: int = 1) -> str:
    if round(x, p) == 0:
        return f"{0:.{p}f}"
    return ("+" if x > 0 else "") + f"{x:,.{p}f}"


# ---------------- SVG 도구
class P:
    """차트 크기별 글자 크기."""

    def __init__(self, W: int):
        self.W, self.m = W, W < 500
        self.fs = 11 if self.m else 12          # 축
        self.fa = 11.5 if self.m else 12.5      # 주석
        self.fb = 15 if self.m else 19          # 강조 숫자


def svg(W: float, H: float, body: str, label: str) -> str:
    return (f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" role="img" aria-label="{esc(label)}" '
            f'style="display:block;overflow:visible">{body}</svg>')


def t(x, y, s, size=12.0, anchor="start", fill=AXIS, weight=400, halo=False) -> str:
    h = ' stroke="#FFFFFF" stroke-width="3" stroke-linejoin="round" paint-order="stroke"' if halo else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}"{h}>{esc(s)}</text>')


def ln(x1, y1, x2, y2, stroke=GRID, w=1.0) -> str:
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{w}"/>'


def pl(pts, stroke, w=2.0) -> str:
    return (f'<polyline fill="none" stroke="{stroke}" stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round" '
            f'points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>')


def dot(x, y, fill, r=4.0) -> str:
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="#FFFFFF" stroke-width="2"/>'


def bar(x, w, y0, y1, fill, rr=3.0) -> str:
    """y0(기준선)에서 y1까지. 끝만 둥글게."""
    h = abs(y1 - y0)
    if h < 0.6:
        return ""
    r = min(rr, w / 2, h)
    if y1 < y0:
        d = (f"M{x:.1f},{y0:.1f}V{y1 + r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}H{x + w - r:.1f}"
             f"Q{x + w:.1f},{y1:.1f} {x + w:.1f},{y1 + r:.1f}V{y0:.1f}Z")
    else:
        d = (f"M{x:.1f},{y0:.1f}V{y1 - r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}H{x + w - r:.1f}"
             f"Q{x + w:.1f},{y1:.1f} {x + w:.1f},{y1 - r:.1f}V{y0:.1f}Z")
    return f'<path d="{d}" fill="{fill}"/>'


def hit(x, y, w, h, tip) -> str:
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 1):.1f}" height="{max(h, 1):.1f}" fill="transparent">'
            f'<title>{esc(tip)}</title></rect>')


def ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min(s * mag for s in (1, 2, 2.5, 3, 5, 10) if s * mag >= raw - 1e-12)
    v = math.floor(lo / step + 1e-9) * step
    out = []
    while True:
        out.append(round(v, 10))
        if v >= hi - 1e-9 or len(out) > 12:
            break
        v += step
    return out


def numf(tk: list[float], signed: bool = False):
    step = abs(tk[1] - tk[0]) if len(tk) > 1 else 1
    d = next((k for k in range(4) if abs(step * 10 ** k - round(step * 10 ** k)) < 1e-6), 3)

    def f(v: float) -> str:
        if signed:
            return "0" if round(v, d) == 0 else sgn(v, d)
        return f"{v:,.{d}f}"
    return f


def ygrid(p: P, vals: list[float], Y, x1: float, fmt, unit_top: str = "") -> str:
    """가로 격자 + 격자 위에 얹은 왼쪽 눈금. 맨 위 눈금에만 단위."""
    out = []
    for i, v in enumerate(vals):
        y = Y(v)
        out.append(ln(0, y, x1, y, G1 if v == 0 else GRID))
        out.append(t(0, y - 5, fmt(v) + (unit_top if i == len(vals) - 1 else ""), p.fs))
    return "".join(out)


def qaxis(p: P, keys: list[str], X, y: float) -> str:
    """분기 축: 위에 1Q~4Q, 아래에 연도(그 해 첫 분기 아래)."""
    out, seen, last_x = [], set(), -1e9
    for i, k in enumerate(keys):
        qn, yy = k[:2], "20" + k[2:4]
        out.append(t(X(i), y, qn, p.fs, "middle"))
        if yy not in seen:
            seen.add(yy)
            if X(i) - last_x >= text_w(yy, p.fs) + 8:      # 연말·연초 분기가 붙어 있으면 앞 연도 라벨과 겹친다
                out.append(t(X(i), y + p.fs + 4, yy, p.fs, "middle", MUTED, 500))
                last_x = X(i)
    return "".join(out)


def text_w(s: str, size: float) -> float:
    return sum(size * (0.92 if ord(ch) > 0x2E80 else 0.56) for ch in s)


def _box(x, y, w, h, anchor):
    x0 = x if anchor == "start" else (x - w if anchor == "end" else x - w / 2)
    return (x0, y - h, x0 + w, y + 2)


def _overlap(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


class Units:
    """차트 금액 단위: 큰 회사는 조원, 나머지는 억원."""

    def __init__(self, big: bool):
        self.div, self.word, self.d = (1e12, "조원", 1) if big else (1e8, "억원", 0)

    def v(self, x: float | None) -> float:
        return (x or 0) / self.div

    def s(self, x: float | None) -> str:
        return "–" if x is None else f"{x / self.div:,.{self.d}f}"

    def full(self, x: float | None) -> str:
        return "–" if x is None else f"{x / self.div:,.{self.d}f}{self.word}"


# ---------------- 차트
def ch_opm(W: int, sp: dict, u: Units) -> str:
    """대표 차트: 분기 영업이익률 선 + 중앙값 + 직전 고점·현재 표시."""
    p = P(W)
    H = 230 if p.m else 300
    keys, vals = sp["keys"], sp["vals"]
    n = len(keys)
    top, bot, x0, x1 = 34, 30, 30, W - (62 if p.m else 92)
    tk = ticks(min(0.0, min(vals)), max(vals), 3)
    lo, hi = tk[0], tk[-1]
    X = lambda i: x0 + i / (n - 1) * (x1 - x0)
    Y = lambda v: top + (hi - v) / (hi - lo) * (H - top - bot)
    b = [ygrid(p, tk, Y, W, numf(tk), "%")]
    pts = [(X(i), Y(v)) for i, v in enumerate(vals)]
    base = Y(max(lo, 0.0))
    area = f"M{pts[0][0]:.1f},{base:.1f}L" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f"L{pts[-1][0]:.1f},{base:.1f}Z"
    b.append(f'<path d="{area}" fill="{ACC}" fill-opacity="0.08"/>')
    med = sp["median"]
    b.append(ln(x0, Y(med), x1, Y(med), G1, 1))
    b.append(pl(pts, ACC, 2.5))
    for i, k in enumerate(keys):
        yy = "20" + k[2:4]
        if k.startswith("1Q") and (not p.m or int(yy) % 2 == 0):
            b.append(t(X(i), H - 8, yy, p.fs, "middle"))
    # 현재 값(오른쪽 여백)
    cx, cy = pts[-1]
    b.append(dot(cx, cy, ACC, 5))
    b.append(t(cx + 10, cy + 6, f"{vals[-1]:.1f}%", p.fb, "start", INK, 700))
    b.append(t(cx + 10, cy + 6 + p.fa + 6, sp.get("current_label", keys[-1]), p.fa, "start", MUTED))
    cur_box = (cy + 6 - p.fb, cy + 6 + p.fa + 8)
    # 중앙값 라벨: 현재 값 라벨과 겹치면 위나 아래로 비킨다
    my = Y(med) - 2
    if not (my + p.fs + 3 < cur_box[0] or my - p.fs > cur_box[1]):
        my = cur_box[1] + p.fs + 4 if Y(med) > cy else cur_box[0] - p.fs - 6
    b.append(t(x1 + 10, my, "중앙값", p.fs, "start", MUTED))
    b.append(t(x1 + 10, my + p.fs + 3, f"{med:.1f}%", p.fs, "start", MUTED))
    # 직전 고점
    if sp.get("peak"):
        pk, pv = sp["peak"]
        if pk in keys:
            px_, py_ = pts[keys.index(pk)]
            b.append(dot(px_, py_, ACC))
            lab = f"직전 고점({pk})"
            wlab = text_w(lab, p.fa)
            anchor = "start" if px_ - wlab / 2 < 0 else "middle"
            if py_ - 24 - p.fa < top - 26:
                y1_, y2_ = py_ + 20, py_ + 34
            else:
                y1_, y2_ = py_ - 24, py_ - 10
            b.append(t(px_, y1_, f"{pv:.1f}%", p.fa + 1, anchor, INK, 700, halo=True))
            b.append(t(px_, y2_, lab, p.fa, anchor, MUTED, halo=True))
    w = (x1 - x0) / max(n - 1, 1)
    for i, k in enumerate(keys):
        b.append(hit(X(i) - w / 2, top, w, H - top - bot, f"{k} 영업이익률 {vals[i]:.1f}%"))
    return svg(W, H, "".join(b), sp.get("title", "분기 영업이익률"))


def ch_bars(W: int, sp: dict, u: Units) -> str:
    """분기 금액 막대(영업이익·순이익). 마지막 분기만 강조, 컨센서스는 가로선."""
    p = P(W)
    H = 230 if p.m else 270
    keys, vals = sp["keys"], [u.v(v) for v in sp["vals"]]
    cons = u.v(sp["cons"]) if sp.get("cons") else None
    n = len(keys)
    top, bot, x0, x1 = 26, 40, 30, W
    slot = (x1 - x0) / n
    bw = min(16 if p.m else 30, slot * 0.62)
    tk = ticks(min(0.0, min(vals)), max(vals + ([cons] if cons else [])), 4)
    Y = lambda v: top + (tk[-1] - v) / (tk[-1] - tk[0]) * (H - top - bot)
    X = lambda i: x0 + slot * (i + 0.5)
    what = sp.get("what", "영업이익")
    b = [ygrid(p, tk, Y, W, numf(tk))]
    for i, (k, v) in enumerate(zip(keys, vals)):
        b.append(bar(X(i) - bw / 2, bw, Y(0), Y(v), ACC if i == n - 1 else TINT))
        b.append(hit(X(i) - slot / 2, top, slot, H - top - bot, f"{k} {what} {u.full(sp['vals'][i])}"))
    b.append(qaxis(p, keys, X, H - bot + 16))
    xl, v = X(n - 1), vals[-1]
    b.append(t(xl, Y(v) - 7 if v >= 0 else Y(v) + p.fa + 4, f"{v:,.{u.d}f}", p.fa + 1, "middle", INK, 700, halo=True))
    if cons:
        yc = Y(cons)
        b.append(ln(xl - bw / 2 - 5, yc, xl + bw / 2 + 5, yc, INK, 1.5))
        b.append(t(xl - bw / 2 - 9, yc + 4, f"컨센서스 {cons:,.{u.d}f}", p.fa, "end", SUB, 500, halo=True))
    return svg(W, H, "".join(b), sp.get("title", what))


def ch_seg(W: int, sp: dict, u: Units) -> str:
    """부문별 분기 영업이익: 양수는 위로, 음수는 아래로 쌓는다."""
    p = P(W)
    keys, series = sp["keys"], [(nm, ROLE.get(role, G1), [u.v(v) for v in vs]) for nm, role, vs in sp["series"]]
    n = len(keys)
    pos = [sum(max(0.0, s[2][i]) for s in series) for i in range(n)]
    neg = [sum(min(0.0, s[2][i]) for s in series) for i in range(n)]
    H0 = 250 if p.m else 290
    top, bot, x0, x1 = 26, 40, 30, W
    slot = (x1 - x0) / n
    bw = min(18 if p.m else 32, slot * 0.6)
    tk = ticks(0.0, max(pos) or 1.0, 4)
    neg_min = min(neg)
    # 음수가 작으면 눈금 없이 기준선 아래 여백에 그린다(목업 방식), 크면 음수 눈금을 둔다
    below = 48 if neg_min < 0 else 0
    small = True
    if neg_min < 0:
        kk_try = (H0 - top - bot - below) / tk[-1]
        need = -neg_min * kk_try + 30
        if need > below:
            if need <= 0.35 * H0:
                below = need
            else:
                small = False
    H = H0
    if small:
        base = H - bot - below
        kk = (base - top) / tk[-1]
        Y = lambda v: base - v * kk
        grid = tk
    else:
        grid = ticks(neg_min, max(pos) or 1.0, 4)
        base = None
        Y = lambda v: top + (grid[-1] - v) / (grid[-1] - grid[0]) * (H - top - bot)
    X = lambda i: x0 + slot * (i + 0.5)
    b = [ygrid(p, grid, Y, W, numf(grid))]
    for i, k in enumerate(keys):
        x = X(i) - bw / 2
        ups = [(c, s[i]) for _, c, s in series if s[i] > 0]
        dns = [(c, s[i]) for _, c, s in series if s[i] < 0]
        for seq, sign in ((ups, 1), (dns, -1)):
            acc = 0.0
            for j, (c, v) in enumerate(seq):
                y0, y1 = Y(acc), Y(acc + v)
                if j > 0:
                    y0 -= 2 * sign
                b.append(bar(x, bw, y0, y1, c, 3 if j == len(seq) - 1 else 0))
                acc += v
        tip = ", ".join(f"{nm} {s[i]:,.{u.d}f}" for nm, _, s in series)
        b.append(hit(X(i) - slot / 2, top, slot, H - top - bot, f"{k} 영업이익({u.word}) {tip}"))
    b.append(qaxis(p, keys, X, H - bot + 16))
    xl = X(n - 1)
    if sp.get("focal_label"):
        b.append(t(xl + bw / 2, Y(pos[-1]) - 7, sp["focal_label"], p.fa, "end", INK, 700, halo=True))
    if sp.get("neg_label") and neg[-1] < 0:
        if small:
            b.append(ln(xl, Y(neg[-1]) + 2, xl, base + 16 + (below - 48), SUB, 1))
            b.append(t(xl + bw / 2, base + 30 + (below - 48), sp["neg_label"], p.fa, "end", INK, 600, halo=True))
        else:
            b.append(t(xl - bw / 2 - 6, Y(neg[-1]) + 4, sp["neg_label"], p.fa, "end", INK, 600, halo=True))
    return svg(W, H, "".join(b), sp.get("title", "부문별 영업이익"))


def ch_band(W: int, sp: dict, u: Units) -> str:
    """월말 주가와 PBR 배수별 주가(BPS × 배수)."""
    p = P(W)
    H = 240 if p.m else 290
    rows, mults = sp["rows"], sp["mults"]
    n = len(rows)
    top, bot, x0, x1 = 26, 30, 30, W - (34 if p.m else 40)
    hi = max(max(r[1] for r in rows), max(r[2] for r in rows) * mults[-1])
    k = 1000 if max(r[1] for r in rows) > 10_000 else 1
    tk = ticks(0, hi / k, 4)
    X = lambda i: x0 + i / (n - 1) * (x1 - x0)
    Y = lambda v: top + (1 - v / (tk[-1] * k)) * (H - top - bot)
    b = [ygrid(p, [v * k for v in tk], Y, W, lambda v: numf(tk)(v / k))]
    last_y = []
    for m in mults:
        pts = [(X(i), Y(r[2] * m)) for i, r in enumerate(rows)]
        b.append(pl(pts, G2, 1.2))
        last_y.append((pts[-1][1], m))
    prev = None
    for y, m in sorted(last_y):
        if prev is not None and y - prev < p.fs + 1:
            y = prev + p.fs + 1
        b.append(t(x1 + 6, y + 4, f"{m:g}배", p.fs, "start", MUTED))
        prev = y
    pts = [(X(i), Y(r[1])) for i, r in enumerate(rows)]
    b.append(pl(pts, ACC, 2.2))
    w = (x1 - x0) / max(n - 1, 1)
    for i, r in enumerate(rows):
        if r[0][5:7] == "01" and int(r[0][:4]) % (4 if p.m else 2) == 0:
            b.append(t(X(i), H - 8, r[0][:4], p.fs, "middle"))
        b.append(hit(X(i) - w / 2, top, w, H - top - bot,
                     f"{r[0][:7].replace('-', '.')} 주가 {r[1]:,.0f}원, PBR {r[1] / r[2]:.2f}배"))
    imin, imax = sp.get("min"), sp.get("max")
    for idx, below in ((imin, True), (imax, False)):
        if idx is None or idx >= n:
            continue
        r = rows[idx]
        x, y = pts[idx]
        b.append(dot(x, y, INK, 3.5))
        v1, v2 = f"{r[1] / r[2]:.2f}배", f"{r[0][:4]}.{int(r[0][5:7])}"
        if below and y + 18 + p.fa + 3 <= H - bot - 2:
            anchor = "start" if x - text_w(v1, p.fa) / 2 < 2 else "middle"
            b.append(t(x, y + 18, v1, p.fa, anchor, INK, 700, halo=True))
            b.append(t(x, y + 18 + p.fa + 3, v2, p.fa, anchor, MUTED, halo=True))
        elif below:                                    # 바닥에 붙어 있으면 점 왼쪽 위에 둔다
            b.append(t(x - 9, y - 8 - p.fa - 3, v1, p.fa, "end", INK, 700, halo=True))
            b.append(t(x - 9, y - 8, v2, p.fa, "end", MUTED, halo=True))
        else:
            b.append(t(x - 10, y + 2, v1, p.fa, "end", INK, 700, halo=True))
            b.append(t(x - 10, y + 2 + p.fa + 3, v2, p.fa, "end", MUTED, halo=True))
    b.append(dot(*pts[-1], ACC, 4.5))
    return svg(W, H, "".join(b), sp.get("title", "PBR 밴드"))


def ch_peers(W: int, sp: dict, u: Units) -> str:
    """ROE(가로)·PBR(세로) 산점도. 라벨은 겹치지 않는 자리를 찾아 붙인다."""
    p = P(W)
    H = 260 if p.m else 300
    rows = sp["rows"]
    top, bot, x0, x1 = 26, 34, 30, W - 8
    roes, pbrs = [r[1] for r in rows], [r[2] for r in rows]
    xs = ticks(min(0.0, min(roes)), max(roes), 4 if p.m else 5)
    ys = ticks(0.0, max(pbrs), 3)
    X = lambda v: x0 + (v - xs[0]) / (xs[-1] - xs[0]) * (x1 - x0)
    Y = lambda v: top + (1 - v / ys[-1]) * (H - top - bot)
    b = [ygrid(p, ys, Y, W, numf(ys), "배")]
    if xs[0] < 0 < xs[-1]:
        b.append(ln(X(0), top, X(0), Y(0), G2))
    xf = numf(xs)
    for v in xs:
        b.append(t(X(v), H - 10, xf(v) + ("%" if v == xs[-1] else ""), p.fs, "middle"))
    pts = [(X(r[1]), Y(r[2]), r) for r in rows]
    taken = [(x - 7, y - 7, x + 7, y + 7) for x, y, _ in pts]
    bounds = (0, top - 16, W, H - bot + 4)
    labels = []
    for x, y, r in sorted(pts, key=lambda q: not q[2][3]):       # 자기 회사 라벨을 먼저 놓는다
        name, me = r[0], r[3]
        rad = 6 if me else 4.5
        w, h = text_w(name, p.fa), p.fa
        cands = [(x + rad + 5, y + 4, "start"), (x - rad - 5, y + 4, "end"), (x, y - rad - 6, "middle"),
                 (x, y + rad + h + 2, "middle"), (x + rad + 3, y - rad - 3, "start"), (x - rad - 3, y - rad - 3, "end"),
                 (x + rad + 3, y + rad + h, "start"), (x - rad - 3, y + rad + h, "end")]
        choice = cands[0]
        for cx, cy, an in cands:
            bx = _box(cx, cy, w, h, an)
            if bx[0] < bounds[0] or bx[2] > bounds[2] or bx[1] < bounds[1] or bx[3] > bounds[3]:
                continue
            if any(_overlap(bx, o) for o in taken):
                continue
            choice = (cx, cy, an)
            break
        taken.append(_box(*choice[:2], w, h, choice[2]))
        labels.append((choice, name, me))
    for x, y, r in sorted(pts, key=lambda q: q[2][3]):
        me = r[3]
        b.append(dot(x, y, ACC if me else G1, 6 if me else 4.5))
        b.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="13" fill="transparent"><title>{esc(r[0])} ROE {r[1]:.1f}%, '
                 f'PBR {r[2]:.2f}배</title></circle>')
    for (cx, cy, an), name, me in labels:
        b.append(t(cx, cy, name, p.fa, an, INK if me else SUB, 700 if me else 400, halo=True))
    return svg(W, H, "".join(b), sp.get("title", "ROE와 PBR"))


def ch_cash(W: int, sp: dict, u: Units) -> str:
    """잉여현금흐름·기말 순현금 묶음 막대."""
    p = P(W)
    H = 230 if p.m else 260
    cols = sp["cols"]
    top, bot, x0, x1 = 26, 30, 30, W
    vals = [u.v(c[1]) for c in cols] + [u.v(c[2]) for c in cols]
    tk = ticks(min(0.0, min(vals)), max(vals), 4)
    Y = lambda v: top + (tk[-1] - v) / (tk[-1] - tk[0]) * (H - top - bot)
    n = len(cols)
    slot = (x1 - x0) / n
    bw = min(20 if p.m else 34, slot * 0.3)
    b = [ygrid(p, tk, Y, W, numf(tk))]
    for i, (lab, fcf, nc) in enumerate(cols):
        cx = x0 + slot * (i + 0.5)
        if fcf is not None:
            b.append(bar(cx - bw - 1.5, bw, Y(0), Y(u.v(fcf)), ACC))
        if nc is not None:
            b.append(bar(cx + 1.5, bw, Y(0), Y(u.v(nc)), TINT))
        b.append(t(cx, H - 8, lab if not p.m else lab.replace(" 상반기", " H1").replace(" 3분기", " 9M").replace(" 1분기", " Q1"),
                   p.fs, "middle"))
        b.append(hit(x0 + slot * i, top, slot, H - top - bot, f"{lab} 잉여현금흐름 {u.full(fcf)}, 순현금 {u.full(nc)}"))
    lab, fcf, nc = cols[-1]
    cx = x0 + slot * (n - 0.5)
    if fcf is not None:
        v = u.v(fcf)
        b.append(t(cx - bw / 2 - 1.5, Y(v) - 7 if v >= 0 else Y(v) + p.fa + 4, f"{v:,.{u.d}f}", p.fa, "middle", INK, 700, halo=True))
    if nc is not None:
        v = u.v(nc)
        b.append(t(cx + bw / 2 + 1.5, Y(v) - 7 if v >= 0 else Y(v) + p.fa + 4, f"{v:,.{u.d}f}", p.fa, "middle", INK, 700, halo=True))
    return svg(W, H, "".join(b), sp.get("title", "잉여현금흐름과 순현금"))


def ch_events(W: int, sp: dict, u: Units) -> str:
    """잠정실적 발표 후 3거래일 지수 대비 초과수익률."""
    p = P(W)
    H = 220 if p.m else 250
    rows = sp["rows"]
    n = len(rows)
    keys = [r[0] for r in rows]
    top, bot, x0, x1 = 26, 40, 30, W
    vals = [r[1] for r in rows]
    tk = ticks(min(0.0, min(vals)), max(0.0, max(vals)), 4)
    Y = lambda v: top + (tk[-1] - v) / (tk[-1] - tk[0]) * (H - top - bot)
    slot = (x1 - x0) / n
    bw = min(12 if p.m else 22, slot * 0.6)
    X = lambda i: x0 + slot * (i + 0.5)
    b = [ygrid(p, tk, Y, W, numf(tk, signed=True), "%p")]
    for i, (q, v, d, yoy) in enumerate(rows):
        b.append(bar(X(i) - bw / 2, bw, Y(0), Y(v), UP if v > 0 else DN, 2))
        g = f"영업이익 YoY {sgn(yoy)}%, " if yoy is not None else ""
        b.append(hit(X(i) - slot / 2, top, slot, H - top - bot, f"{d.replace('-', '.')} {q} 발표, {g}3거래일 {sgn(v)}%p"))
    for i in range(max(0, n - 3), n):
        v = vals[i]
        b.append(t(X(i), Y(v) + (14 if v < 0 else -6), sgn(v), p.fa, "middle", INK, 700, halo=True))
    b.append(qaxis(p, keys, X, H - bot + 16))
    return svg(W, H, "".join(b), sp.get("title", "잠정실적 발표 반응"))


def ch_rel(W: int, sp: dict, u: Units) -> str:
    """최근 1년 주가와 지수(시작일=100)."""
    from datetime import date as _d

    p = P(W)
    H = 220 if p.m else 250
    rows = sp["rows"]
    n = len(rows)
    name, idx = sp.get("name", "주가"), sp.get("index", "KOSPI")
    lab_s, lab_i = f"{name} {rows[-1][1]:.0f}", f"{idx} {rows[-1][2]:.0f}"
    gutter = max(text_w(lab_s, p.fa), text_w(lab_i, p.fa)) + 22
    top, bot, x0, x1 = 26, 30, 30, W - gutter
    lo = min(min(r[1], r[2]) for r in rows)
    hi = max(max(r[1], r[2]) for r in rows)
    tk = ticks(lo, hi, 3 if p.m else 4)
    Y = lambda v: top + (tk[-1] - v) / (tk[-1] - tk[0]) * (H - top - bot)
    X = lambda i: x0 + i / (n - 1) * (x1 - x0)
    b = [ygrid(p, tk, Y, W, numf(tk))]
    b.append(pl([(X(i), Y(r[2])) for i, r in enumerate(rows)], G1, 2))
    sp_pts = [(X(i), Y(r[1])) for i, r in enumerate(rows)]
    b.append(pl(sp_pts, ACC, 2.2))
    dates = [r[0] for r in rows]
    for e in sp.get("events", []):
        j = min(range(n), key=lambda k: abs((_d.fromisoformat(dates[k]) - _d.fromisoformat(e)).days))
        b.append(f'<circle cx="{sp_pts[j][0]:.1f}" cy="{sp_pts[j][1]:.1f}" r="4" fill="#FFFFFF" stroke="{ACC}" '
                 f'stroke-width="2"><title>{e.replace("-", ".")} 잠정실적 발표</title></circle>')
    for i in range(0, n, max(1, n // (3 if p.m else 4))):
        if X(i) < x1 - 20:
            b.append(t(X(i), H - 8, dates[i][:7].replace("-", "."), p.fs, "middle"))
    ys, yi = sp_pts[-1][1], Y(rows[-1][2])
    if abs(ys - yi) < p.fa + 3:                       # 두 끝 라벨이 겹치면 벌린다
        mid = (ys + yi) / 2
        gap = (p.fa + 3) / 2
        ys, yi = (mid - gap, mid + gap) if ys <= yi else (mid + gap, mid - gap)
    b.append(dot(*sp_pts[-1], ACC, 4))
    b.append(t(sp_pts[-1][0] + 10, ys + 4, lab_s, p.fa, "start", INK, 700))
    b.append(dot(X(n - 1), Y(rows[-1][2]), G1, 4))
    b.append(t(X(n - 1) + 10, yi + 4, lab_i, p.fa, "start", SUB, 500))
    step = max(1, n // 60)
    for i in range(0, n, step):
        r = rows[i]
        w = (x1 - x0) / n * step
        raw = f"{name} {r[3]:,.0f}원, {idx} {r[4]:,.2f}" if len(r) > 4 else f"{name} {r[1]:.1f}, {idx} {r[2]:.1f}"
        b.append(hit(X(i) - w / 2, top, w, H - top - bot, f"{r[0].replace('-', '.')} {raw}"))
    return svg(W, H, "".join(b), sp.get("title", "1년 주가"))


def figure(fn, sp: dict, u: Units, legend: list | None = None, cls: str = "") -> str:
    try:
        d, m = fn(760, sp, u), fn(340, sp, u)
    except Exception as e:  # noqa: BLE001
        log.exception("차트 실패 %s: %s", sp.get("title"), e)
        return ""
    lg = ""
    if legend:
        items = []
        for nm, kind, color in legend:
            c = ROLE.get(color, color)
            k = "ln" if kind == "line" else ("dt" if kind == "dot" else "")
            items.append(f'<span><i class="{k}" style="background:{c}"></i>{esc(nm)}</span>')
        lg = f'<div class="lg">{"".join(items)}</div>'
    return (f'<figure class="chart {cls}"><figcaption><h4>{esc(sp["title"])}</h4><p class="st">{esc(sp["subtitle"])}</p>'
            f'{lg}</figcaption><div class="cv d">{d}</div><div class="cv m">{m}</div>'
            + (f'<p class="src">자료: {esc(sp["source"])}</p>' if sp.get("source") else "") + "</figure>")


# ---------------- 표
def _cell(x) -> tuple[str, str]:
    if isinstance(x, dict):
        s, v = x.get("sign"), esc(x.get("v", "–"))
        if s is None or round(s, 1) == 0:
            return v, ""
        return f'<span class="{"up" if s > 0 else "dn"}">{v}</span>', ""
    return esc(x), ""


def table_html(sp: dict) -> str:
    heads, focal, tips = sp["heads"], sp.get("focal"), sp.get("tips") or {}
    th = ['<th scope="col"></th>']
    for i, h in enumerate(heads):
        a = f' title="{esc(tips[h])}"' if tips.get(h) else ""
        th.append(f'<th scope="col"{a}{" class=f" if i == focal else ""}>{esc(h)}</th>')
    body = []
    for r in sp["rows"]:
        sub = r.get("sub")
        a = f' title="{esc(r["tip"])}"' if r.get("tip") else ""
        cells = []
        for i, x in enumerate(r["cells"]):
            v, _ = _cell(x)
            cl = " ".join(c for c in ("f" if i == focal else "", "sub" if sub else "") if c)
            cells.append(f'<td class="{cl}">{v}</td>' if cl else f"<td>{v}</td>")
        body.append(f'<tr{" class=b" if r.get("bold") else ""}><th scope="row"{" class=sub" if sub else ""}{a}>'
                    f'{esc(r["head"])}</th>{"".join(cells)}</tr>')
    return (f'<div class="tbl"><h4>{esc(sp["title"])}</h4><p class="st">{esc(sp["unit"])}</p><div class="tw">'
            f'<table class="t"><thead><tr>{"".join(th)}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
            + (f'<p class="src">자료: {esc(sp["source"])}</p>' if sp.get("source") else "") + "</div>")


# ---------------- 문장
_CITES = re.compile(r"(?:\s*\[(?:n\d+(?:\s*,\s*n\d+)*)\])+")


class Text:
    """{{슬롯}} → 표시값(출처 툴팁), [n1] → 위첨자 각주 번호."""

    def __init__(self, facts: dict[str, Metric], news: list[dict]):
        self.facts = facts
        self.no = {n["id"]: n["no"] for n in news}
        self.news = {n["id"]: n for n in news}

    def __call__(self, s: str) -> str:
        out = esc(s)

        def slot(mt: Metric | None, shown: str) -> str:
            if mt is None:
                log.warning("렌더: 없는 슬롯 %s", shown)
                return "–"
            return f'<span class="m" title="{esc(tooltip(mt))}">{esc(shown)}</span>'

        def cite(m: re.Match) -> str:
            ids = sorted({i for i in re.findall(r"n\d+", m.group(0)) if i in self.no}, key=lambda i: self.no[i])
            if not ids:
                return ""
            tips = [f"{self.news[i].get('outlet') or ''}, {_md(self.news[i].get('date'))}: {self.news[i].get('title') or ''}"
                    for i in ids]
            return f'<sup class="fn" title="{esc(" / ".join(tips))}">{",".join(str(self.no[i]) for i in ids)}</sup>'
        out = _CITES.sub(cite, out)            # 각주를 먼저 바꾼다(슬롯 툴팁 안의 대괄호를 건드리지 않게)
        return substitute(out, self.facts, slot)


def _md(d: str | None) -> str:
    if not d:
        return ""
    return f"{int(d[5:7])}월 {int(d[8:10])}일"


def blocks_html(items: list[Block], tx: Text, cls: str = "blk") -> str:
    return "".join(f'<div class="{cls}"><h3>{tx(b.head)}</h3><p>{tx(b.text)}</p></div>' for b in items)


def _safe_url(u: str | None) -> str:
    return esc(u) if u and re.match(r"^https?://", u) else "#"


def key_data_html(items: list[dict]) -> str:
    out = []
    for k in items:
        sub = esc(k.get("sub") or "")
        if k.get("sign") is not None and sub:
            first, _, rest = sub.partition(" ")
            if round(k["sign"], 2) != 0:
                first = f'<span class="{"up" if k["sign"] > 0 else "dn"}">{first}</span>'
            sub = f"{first} {rest}".strip()
        if k.get("sub_sign") is not None and sub:
            head, _, last = sub.rpartition(" ")
            if round(k["sub_sign"], 1) != 0:
                last = f'<span class="{"up" if k["sub_sign"] > 0 else "dn"}">{last}</span>'
            sub = f"{head} {last}".strip()
        out.append(f'<div><dt>{esc(k["label"])}</dt><dd>{esc(k["value"])}<small>{sub}</small></dd></div>')
    return "".join(out)


def news_html(news: list[dict], disc: list[dict]) -> str:
    out = []
    if news:
        items = "".join(
            f'<li><span class="no">{n["no"]}</span><div><p class="meta">{esc(n.get("outlet") or "")}, {esc(_md(n.get("date")))}</p>'
            f'<a href="{_safe_url(n.get("url"))}">{esc(n.get("title") or "")}</a>'
            + (f'<p class="gist">{esc(n["gist"])}</p>' if n.get("gist") else "") + "</div></li>" for n in news)
        out.append(f'<h3 class="lt">주요 뉴스</h3><ol class="news">{items}</ol>')
    if disc:
        items = "".join(
            f'<li><p class="meta">{esc(_md(d.get("date")))}</p><a href="{_safe_url(d.get("url"))}">{esc(d.get("title") or "")}</a>'
            + (f'<p class="gist">{esc(d["gist"])}</p>' if d.get("gist") else "") + "</li>" for d in disc)
        out.append(f'<h3 class="lt">최근 공시</h3><ul class="news disc">{items}</ul>')
    return "".join(out)


# ---------------- 조립
def _legend(kind: str, sp: dict) -> list | None:
    if kind == "op":
        return sp.get("legend")
    if kind == "segments":
        return [[nm, "bar", role] for nm, role, _ in sp["series"]]
    if kind == "band":
        ms = sp["mults"]
        return [["주가", "line", "acc"], [f"PBR {ms[0]:g}~{ms[-1]:g}배", "line", G2]]
    if kind == "cash":
        return [["잉여현금흐름", "bar", "acc"], ["순현금", "bar", "tint"]]
    return None


CHARTS = {"op": ch_bars, "segments": ch_seg, "band": ch_band, "peers": ch_peers, "cash": ch_cash,
          "events": ch_events, "rel": ch_rel}


def render_report(data, draft: ReportDraft | None, *, fs_div: str | None = "CFS") -> str:
    """data: analysis.ReportData. draft가 없으면(문장 생성 실패) 차트·표만 그린다."""
    u = Units(data.table_div == 1e9)
    tx = Text(data.facts, data.news)
    corp = data.corp
    made = date.fromisoformat(data.made)
    grp = (lambda g: getattr(draft, g)) if draft else (lambda g: [])

    names = {**SECTION_NAMES, "cash": "주주환원"} if corp.get("industry") == "financial" else SECTION_NAMES

    def head(field: str, sec: str) -> str:
        h: Headline | None = getattr(draft, field, None) if draft else None
        text = re.sub(rf"^\s*({re.escape(names[sec])}|{re.escape(SECTION_NAMES[sec])})\s*:\s*", "", h.text) \
            if h and h.text else ""
        return f"<h2>{esc(names[sec])}" + (f": {tx(text)}" if text else "") + "</h2>"

    def chart(kind: str, cls: str = "") -> str:
        sp = data.charts.get(kind)
        return figure(CHARTS[kind], sp, u, _legend(kind, sp), cls) if sp else ""

    def table(kind: str) -> str:
        sp = data.tables.get(kind)
        return table_html(sp) if sp and isinstance(sp, dict) and sp.get("rows") else ""

    hero = data.charts.get("hero")
    title = tx(draft.title.text) if draft else esc(hero["title"] if hero else corp["name"])
    lead = ""
    if hero:
        if hero.get("kind") == "ni":
            lead = figure(ch_bars, {**hero, "what": "지배주주 순이익" if fs_div != "OFS" else "순이익"}, u, None, "lead")
        else:
            lead = figure(ch_opm, hero, u, None, "lead")
    points = blocks_html(grp("summary"), tx, "pt")
    sections = []
    parts = [blocks_html(grp("earnings_a"), tx), chart("op"), blocks_html(grp("earnings_b"), tx), chart("segments"),
             table("quarters")]
    sections.append(("earnings", parts))
    parts = [blocks_html(grp("valuation_a"), tx), chart("band"), blocks_html(grp("valuation_b"), tx), chart("peers"),
             table("peers")]
    sections.append(("valuation", parts))
    parts = [blocks_html(grp("cash_a"), tx), chart("cash"), blocks_html(grp("cash_b"), tx), table("cash")]
    sections.append(("cash", parts))
    parts = [blocks_html(grp("market_a"), tx), chart("rel"), blocks_html(grp("market_b"), tx), chart("events"),
             blocks_html(grp("market_c"), tx), news_html(data.news, data.disclosures)]
    sections.append(("market", parts))
    sec_html = "".join(f"<section>{head(sec + '_head', sec)}{''.join(p)}</section>"
                       for sec, p in sections if any(p))
    basis = "연결·지배주주 기준" if fs_div != "OFS" else "별도 재무제표 기준"
    body = (
        f'<main class="doc"><header class="mast"><span class="brand">Finsight</span>'
        f'<span class="date">{made.year}년 {made.month}월 {made.day}일</span></header>'
        f'<section class="hero"><p class="co">{esc(corp["name"])}<span>{esc(corp.get("stock_code") or "")}</span></p>'
        f'<h1>{title}</h1><dl class="kd">{key_data_html(data.key_data)}</dl></section>'
        f'{lead}' + (f'<section class="points">{points}</section>' if points else "")
        + table("latest") + table("annual") + sec_html
        + f'<footer class="foot">{basis}이며 분기 실적은 3개월 단독(4분기는 연간에서 3분기 누적을 뺀 값). '
          f'본 자료는 공시, 시세, 언론 보도를 바탕으로 작성한 참고 자료이며 투자 권유가 아닙니다.</footer></main>')
    visible = html.unescape(re.sub(r"<[^>]+>", " ", body) + " ".join(re.findall(r'title="([^"]*)"', body)))
    return f'<style>{font_faces(visible)}\n{CSS}</style><div class="fs-report">{body}</div>'


# ---------------- 폰트·CSS
@lru_cache(maxsize=4)
def _font_bytes(name: str) -> bytes:
    return (FONT_DIR / f"Pretendard-{name}.subset.woff2").read_bytes()


def font_faces(text: str) -> str:
    """문서에 쓰인 글자만 잘라 woff2로 내장. 폰트 파일이 없거나 실패하면 시스템 글꼴로 둔다."""
    try:
        from fontTools import subset as ftsubset
        from fontTools.ttLib import TTFont
    except Exception:  # noqa: BLE001
        return ""
    chars = set(text) | set("0123456789,.%+-–−×~/()[]:;!?\"'“”‘’·… abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    out = []
    for w, name in ((400, "Regular"), (500, "Medium"), (600, "SemiBold"), (700, "Bold")):
        try:
            f = TTFont(io.BytesIO(_font_bytes(name)))
            opt = ftsubset.Options()
            opt.flavor, opt.layout_features, opt.name_IDs, opt.notdef_outline = "woff2", ["*"], ["*"], True
            sub = ftsubset.Subsetter(opt)
            sub.populate(text="".join(sorted(chars)))
            sub.subset(f)
            buf = io.BytesIO()
            f.flavor = "woff2"
            f.save(buf)
        except Exception as e:  # noqa: BLE001
            log.warning("폰트 내장 실패 %s: %s", name, e)
            return ""
        b64 = base64.b64encode(buf.getvalue()).decode()
        out.append(f"@font-face{{font-family:'{FAMILY}';font-weight:{w};font-style:normal;font-display:swap;"
                   f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}")
    return "\n".join(out)


R = ".fs-report"
CSS = f"""
{R}{{container-type:inline-size;background:#FFFFFF;color:{TXT}}}
{R} .doc{{max-width:760px;margin:0 auto;padding:36px 24px 96px;font-family:'{FAMILY}','Pretendard Variable',Pretendard,system-ui,-apple-system,sans-serif;font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased;word-break:keep-all;text-align:left}}
{R} h1,{R} h2,{R} h3,{R} h4,{R} p,{R} dl,{R} dd,{R} figure,{R} ol,{R} ul,{R} li{{margin:0;padding:0}}
{R} ol,{R} ul{{list-style:none}}
{R} h2::before{{content:none;display:none}}
{R} sup{{vertical-align:super;line-height:0}}
{R} .mast{{display:flex;justify-content:space-between;align-items:baseline;padding-bottom:14px;border-bottom:1px solid {GRID};margin-bottom:56px}}
{R} .brand{{font-size:17px;font-weight:700;color:{ACC};letter-spacing:-0.02em}}
{R} .date{{font-size:13px;color:{MUTED}}}
{R} .co{{margin:0 0 12px;font-size:16px;font-weight:600;color:{INK}}}
{R} .co span{{margin-left:8px;font-weight:500;color:{AXIS}}}
{R} h1{{margin:0 0 40px;font-size:38px;line-height:1.28;font-weight:700;letter-spacing:-0.035em;color:{INK};border:0;padding:0}}
{R} .kd{{display:grid;grid-template-columns:repeat(3,1fr);gap:26px 28px;margin:0;padding:26px 0;border-top:1px solid {GRID};border-bottom:1px solid {GRID}}}
{R} .kd div{{min-width:0}}
{R} .kd dt{{font-size:13px;color:{MUTED};margin:0 0 6px}}
{R} .kd dd{{margin:0;font-size:23px;font-weight:700;color:{INK};letter-spacing:-0.02em;line-height:1.25}}
{R} .kd small{{display:block;margin-top:5px;font-size:13px;font-weight:500;color:{MUTED};letter-spacing:0}}
{R} .chart{{margin:56px 0 0}}
{R} .chart.lead{{margin-top:64px}}
{R} .chart h4,{R} .tbl h4{{margin:0 0 4px;font-size:18px;font-weight:700;color:{INK};letter-spacing:-0.02em;line-height:1.4}}
{R} .chart.lead h4{{font-size:22px}}
{R} .st{{margin:0 0 14px;font-size:13.5px;color:{MUTED}}}
{R} .lg{{display:flex;flex-wrap:wrap;gap:6px 18px;margin:-4px 0 12px;font-size:13px;color:{SUB}}}
{R} .lg span{{display:inline-flex;align-items:center;gap:7px}}
{R} .lg i{{display:inline-block;width:11px;height:11px;border-radius:3px}}
{R} .lg i.ln{{width:16px;height:3px;border-radius:2px}}
{R} .cv.m{{display:none}}
{R} .chart svg text{{font-family:inherit}}
{R} .src{{margin:12px 0 0;font-size:12px;color:{AXIS}}}
{R} .points{{display:grid;grid-template-columns:1fr 1fr;gap:40px 44px;margin:64px 0 0;padding-top:40px;border-top:1px solid {GRID}}}
{R} .points h3{{margin:0 0 8px;font-size:17px;font-weight:700;color:{INK};letter-spacing:-0.02em;line-height:1.45}}
{R} .points p{{margin:0;font-size:15px;line-height:1.75}}
{R} section > h2{{margin:104px 0 28px;font-size:26px;font-weight:700;letter-spacing:-0.03em;line-height:1.35;color:{INK};display:block}}
{R} .blk{{max-width:680px;margin:0 0 30px}}
{R} .blk h3{{margin:0 0 8px;font-size:17px;font-weight:700;color:{INK};letter-spacing:-0.02em;line-height:1.5}}
{R} .blk p{{margin:0;font-size:16px;line-height:1.8}}
{R} .chart + .blk,{R} .tbl + .blk{{margin-top:48px}}
{R} .m{{cursor:help}}
{R} .m:hover{{text-decoration:underline dotted;text-underline-offset:3px}}
{R} sup.fn{{margin-left:1px;font-size:11px;font-weight:600;color:{ACC};cursor:help}}
{R} .tbl{{margin:56px 0 0}}
{R} .tw{{overflow-x:auto}}
{R} .t{{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums;margin:0}}
{R} .t th,{R} .t td{{padding:11px 8px;text-align:right;white-space:nowrap;border:0;border-bottom:1px solid {FAINT};background:none;color:{TXT}}}
{R} .t thead th{{padding-top:4px;font-size:13px;font-weight:500;color:{MUTED};border-bottom:1px solid {G2}}}
{R} .t th:first-child,{R} .t td:first-child{{padding-left:0;text-align:left}}
{R} .t tbody th{{font-weight:500;color:{TXT}}}
{R} .t [title]{{cursor:help}}
{R} .t td.f{{background:{WASH};color:{INK};font-weight:600}}
{R} .t thead th.f{{background:{WASH};color:{ACC};font-weight:600}}
{R} .t .sub{{font-size:13px;color:{AXIS}}}
{R} .t th.sub{{padding-left:14px;font-weight:400}}
{R} .t tr.b th,{R} .t tr.b td{{font-weight:700;color:{INK}}}
{R} .up{{color:{UP}}}
{R} .dn{{color:{DN}}}
{R} h3.lt{{margin:56px 0 4px;font-size:18px;font-weight:700;color:{INK};letter-spacing:-0.02em}}
{R} .news li{{display:grid;grid-template-columns:26px 1fr;padding:18px 0;border-bottom:1px solid {FAINT}}}
{R} .news.disc li{{display:block}}
{R} .news .no{{font-size:14px;font-weight:700;color:{ACC};line-height:1.6}}
{R} .news .meta{{margin:0 0 3px;font-size:12.5px;color:{AXIS}}}
{R} .news a{{font-size:15.5px;font-weight:600;color:{INK};text-decoration:none;line-height:1.5}}
{R} .news a:hover{{text-decoration:underline;text-underline-offset:3px}}
{R} .news .gist{{margin:6px 0 0;font-size:14px;line-height:1.7;color:{SUB}}}
{R} .foot{{margin-top:80px;padding-top:18px;border-top:1px solid {GRID};font-size:12.5px;line-height:1.7;color:{AXIS}}}
{R} a:focus-visible{{outline:2px solid {ACC};outline-offset:2px;border-radius:2px}}
@container (max-width:600px){{
  {R} .doc{{padding:24px 16px 72px}}
  {R} .mast{{margin-bottom:36px}}
  {R} h1{{font-size:28px;margin-bottom:28px}}
  {R} .kd{{grid-template-columns:1fr 1fr;gap:20px 16px}}
  {R} .kd dd{{font-size:19px}}
  {R} .points{{grid-template-columns:1fr;gap:28px}}
  {R} section > h2{{font-size:22px;margin-top:80px}}
  {R} .chart.lead h4{{font-size:19px}}
  {R} .cv.d{{display:none}}
  {R} .cv.m{{display:block}}
}}
@media print{{
  {R} .doc{{padding:0}}
  {R} .chart,{R} .tbl{{break-inside:avoid}}
}}
"""
