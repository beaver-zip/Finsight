"""리포트 v2 분석: 수집한 원자료(Inputs) → 차트·표·핵심 수치·사실표(ReportData). 외부 I/O 없음.

숫자는 모두 여기서 계산하고, 문장 생성(LLM)은 사실표의 {{id}} 슬롯으로만 숫자를 쓴다.
한 부분의 자료가 없으면 그 부분만 비우고(None) 나머지는 그대로 만든다.
"""
from __future__ import annotations

import logging
import re
import statistics as st
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.core.dart.history import QRow, prev_quarter, qkey
from app.core.dart.prelim import Prelim, PrelimFiling
from app.core.dart.shares import BuybackDecision, Dividend, Shares
from app.core.peers import PeerRow
from app.domain import fmt
from app.domain.accounts import CASH_LIKE, DEBT_LIKE
from app.domain.ksic import industry_short
from app.domain.sources import outlet_name
from app.domain.models import CorpProfile, Disclosure, Metric, NewsItem, PeriodicFiling

log = logging.getLogger(__name__)
QDAYS = {1: 90, 2: 91, 3: 92, 4: 92}


@dataclass
class Consensus:
    period: str                    # "3Q26"
    revenue: float | None          # 원
    operating_income: float | None
    provider: str | None           # "에프앤가이드"
    news_id: str | None
    outlet: str | None
    quote: str | None


@dataclass
class Inputs:
    corp: CorpProfile
    today: date
    filing: PeriodicFiling
    quarters: list[QRow]
    prelim: Prelim | None
    cum: dict[tuple[int, int], dict]
    shares: dict[tuple[int, int], Shares]
    splits: list[tuple[date, float]]
    dividends: dict[int, Dividend]
    buybacks: list[BuybackDecision]
    px: list[tuple[date, float]]
    index_px: list[tuple[date, float]]
    index_name: str
    pref_px: float | None
    prelim_firsts: list[PrelimFiling]
    segments: dict[tuple[int, int], dict[str, dict[str, Decimal]]]
    peers: list[PeerRow]
    consensus: Consensus | None
    news: list[NewsItem]
    disclosures: list[Disclosure]
    industry: str = "general"


def make_inputs(corp: CorpProfile, today: date, filing: PeriodicFiling, col: Any, *, consensus: Consensus | None = None,
                news: list[NewsItem] | None = None, disclosures: list[Disclosure] | None = None) -> Inputs:
    """collect.Collected + 뉴스·공시·컨센서스 → Inputs."""
    return Inputs(corp=corp, today=today, filing=filing, quarters=col.quarters, prelim=col.prelim, cum=col.cum,
                  shares=col.shares, splits=col.splits, dividends=col.dividends, buybacks=col.buybacks, px=col.px,
                  index_px=col.index_px, index_name=col.index_name, pref_px=col.pref_px,
                  prelim_firsts=col.prelim_firsts, segments=col.segments, peers=col.peers, consensus=consensus,
                  news=list(news or []), disclosures=list(disclosures or []), industry=col.industry)


@dataclass
class ReportData:
    corp: dict
    made: str
    asof: str | None
    latest: dict
    table_div: float
    table_unit: str
    key_data: list[dict] = field(default_factory=list)
    charts: dict[str, dict] = field(default_factory=dict)
    tables: dict[str, dict] = field(default_factory=dict)
    news: list[dict] = field(default_factory=list)
    disclosures: list[dict] = field(default_factory=list)
    facts: dict[str, Metric] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"corp": self.corp, "made": self.made, "asof": self.asof, "latest": self.latest,
                "table_unit": self.table_unit, "key_data": self.key_data, "charts": self.charts,
                "tables": self.tables, "news": self.news, "disclosures": self.disclosures,
                "facts": {k: v.model_dump(mode="json") for k, v in self.facts.items()}, "notes": self.notes}


# ---------------- 도구
def f(v: Decimal | float | None) -> float | None:
    return None if v is None else float(v)


def clean_name(name: str) -> str:
    return re.sub(r"\(주\)|㈜|주식회사", "", name or "").strip()


def per_label(key: str) -> str:
    """슬롯 기간: '3Q26P' 그대로, 반기 누적 'H126'처럼 영숫자만."""
    return re.sub(r"[^A-Za-z0-9]", "", key)


def ret_between(px: list[tuple[date, float]], days: int) -> float | None:
    """마지막 종가 ÷ days일 전(휴장일이면 그 전 거래일) 종가 − 1. 이력이 모자라면 None."""
    if len(px) < 2:
        return None
    end_d, end_v = px[-1]
    i = bisect_right([d for d, _ in px], end_d - timedelta(days=days)) - 1
    if i < 0:
        return None
    return (end_v / px[i][1] - 1) * 100


def close_on_or_before(px: list[tuple[date, float]], d: date) -> tuple[date, float] | None:
    ds = [x for x, _ in px]
    i = bisect_right(ds, d) - 1
    return px[i] if i >= 0 else None


class Facts:
    def __init__(self):
        self.items: dict[str, Metric] = {}

    def add(self, mid: str, period: str, label: str, text: str | None, value: float | None = None,
            unit: str = "text", formula: str = "", source: str | None = None) -> str:
        if text is None or text == "–":
            return ""
        m = Metric(id=mid, label=label, period=per_label(period), value=None if value is None else Decimal(str(round(value, 6))),
                   unit=unit, formula=formula or label, text=text, source=source)
        self.items[m.slot_id] = m
        return m.slot_id


# ---------------- 본 계산
def analyze(inp: Inputs) -> ReportData:
    corp = inp.corp
    F = Facts()
    name = clean_name(corp.corp_name)
    qs = [r for r in inp.quarters if r.revenue is not None or r.operating_income is not None]
    by = {r.key: r for r in qs}
    last_periodic = qs[-1] if qs else None
    rows = list(qs)
    pre = inp.prelim
    if pre is not None and last_periodic is not None and (pre.year, pre.q) > (last_periodic.year, last_periodic.q):
        pr = QRow(key=pre.key + "P", year=pre.year, q=pre.q, preliminary=True, rcept_no=pre.rcept_no,
                  revenue=pre.rows["revenue"].cur if "revenue" in pre.rows else None,
                  operating_income=pre.rows["operating_income"].cur if "operating_income" in pre.rows else None,
                  net_income_owners=pre.rows["net_income_owners"].cur if "net_income_owners" in pre.rows else None)
        rows.append(pr)
    else:
        pre = None
    by_all = {r.key: r for r in rows}
    L = rows[-1] if rows else None
    ttm_rev = sum(f(r.revenue) or 0 for r in qs[-4:]) if len(qs) >= 4 else None
    big = (ttm_rev or 0) >= 10e12
    data = ReportData(corp={"name": name, "corp_code": corp.corp_code, "stock_code": corp.stock_code,
                            "corp_cls": corp.corp_cls, "induty_code": corp.induty_code},
                      made=inp.today.isoformat(), asof=inp.px[-1][0].isoformat() if inp.px else None,
                      latest={"key": L.key if L else None, "prelim": pre is not None,
                              "label": (f"{pre.key} 잠정" if pre else (L.key if L else ""))},
                      table_div=1e9 if big else 1e8, table_unit="십억원" if big else "억원")
    src_dart = "DART"
    sections = [("key", _key_data), ("latest", _latest), ("op", _op_chart), ("opm", _opm_history), ("annual", _annual),
                ("quarters", _quarter_table), ("segments", _segments), ("valuation", _valuation),
                ("peers", _peers), ("cash", _cash), ("events", _events), ("rel", _rel), ("news", _news_disc)]
    ctx = dict(inp=inp, F=F, data=data, rows=rows, qs=qs, by=by, by_all=by_all, L=L, pre=pre, name=name, src=src_dart)
    for nm, fn in sections:
        try:
            fn(ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("분석 %s 실패", nm)
            data.notes.append(f"{nm}: {type(e).__name__}: {e}")
    data.facts = F.items
    return data


# ---------------- 핵심 수치
def _key_data(c: dict) -> None:
    inp, F, data, name = c["inp"], c["F"], c["data"], c["name"]
    px = inp.px
    if not px:
        return
    last_d, last = px[-1]
    prev = px[-2][1] if len(px) > 1 else None
    day = (last / prev - 1) * 100 if prev else None
    one_year = [v for d, v in px if d >= last_d - timedelta(days=365)]
    hi, lo = max(one_year), min(one_year)
    r12 = ret_between(px, 365)
    i12 = ret_between(inp.index_px, 365) if inp.index_px else None
    rel12 = r12 - i12 if r12 is not None and i12 is not None else None
    c["last_px"], c["last_d"] = last, last_d
    F.add("px", "now", f"{name} 주가({fmt.md(last_d)})", fmt.won(last), last, "n", source=f"종가 {last_d}")
    if day is not None:
        F.add("px_day", "now", f"{fmt.md(last_d)} 주가 등락률", fmt.spct(day, 2), day, "%")
    F.add("ret12", "now", "12개월 주가 수익률", fmt.spct(r12), r12, "%")
    F.add("rel12", "now", f"12개월 {inp.index_name} 대비 초과수익률", fmt.pp(rel12), rel12, "%p")
    F.add("hi52", "now", "52주 최고 종가", fmt.won(hi), hi, "n")
    F.add("lo52", "now", "52주 최저 종가", fmt.won(lo), lo, "n")
    for lab, days in (("1M", 30), ("3M", 91), ("6M", 182)):
        r = ret_between(px, days)
        F.add(f"ret{lab}", "now", f"{lab} 주가 수익률", fmt.spct(r), r, "%")
    sh = inp.shares.get((inp.filing.bsns_year, _q_of(inp.filing.reprt_code)))
    mcap = None
    if sh:
        mcap = last * sh.common_out + (inp.pref_px * sh.pref_out if inp.pref_px and sh.pref_out else 0)
        F.add("mcap", "now", "시가총액(보통주+우선주)", fmt.money(mcap, 0) if mcap >= 1e14 else fmt.money(mcap), mcap, "KRW")
        if inp.pref_px and sh.pref_out:
            disc = (1 - inp.pref_px / last) * 100
            c["pref_disc"] = disc
            F.add("pref_disc", "now", "우선주 괴리율(보통주 대비 할인)", fmt.pct(disc), disc, "%", source=f"{fmt.md(last_d)} 종가")
            F.add("pref_px", "now", "우선주 주가", fmt.won(inp.pref_px), inp.pref_px, "n")
    c["mcap"] = mcap
    data.key_data = [
        {"label": "주가", "value": fmt.won(last), "sub": f"{fmt.spct(day, 2)} {fmt.md(last_d)}" if day is not None else fmt.md(last_d),
         "sign": day},
        {"label": "시가총액", "value": (f"{mcap / 1e12:,.0f}조원" if mcap and mcap >= 1e13 else fmt.money(mcap)) if mcap else "–",
         "sub": "보통주·우선주 합산" if (sh and sh.pref_out and inp.pref_px) else "보통주"},
    ]
    data.extra["key_tail"] = {"r12": r12, "rel12": rel12, "hi": hi, "lo": lo, "index": inp.index_name}


def _q_of(rc: str) -> int:
    return {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[rc]


# ---------------- 최근 분기
def _growth(cur: float | None, base: float | None) -> float | None:
    if cur is None or base is None or base <= 0 or cur <= 0:
        return None
    return (cur / base - 1) * 100


def _latest(c: dict) -> None:
    F, data, rows, by_all, L, pre = c["F"], c["data"], c["rows"], c["by_all"], c["L"], c["pre"]
    if L is None:
        return
    y, q = L.year, L.q
    P = by_all.get(qkey(*prev_quarter(y, q)))
    Y = by_all.get(qkey(y - 1, q))
    lk = L.key
    lab = data.latest["label"]
    rev, op = f(L.revenue), f(L.operating_income)
    opm = op / rev * 100 if rev and op is not None else None
    c["opm_L"] = opm
    srcL = f"영업(잠정)실적 공시 rcpNo {pre.rcept_no}" if pre else f"DART rcpNo {L.rcept_no}"
    F.add("rev", lk, f"매출액({lab})", fmt.money(rev), rev, "KRW", source=srcL)
    F.add("op", lk, f"영업이익({lab})", fmt.money(op), op, "KRW", source=srcL)
    F.add("opm", lk, f"영업이익률({lab})", fmt.pct(opm), opm, "%")
    if L.net_income_owners is not None:
        F.add("ni", lk, f"지배주주 순이익({lab})", fmt.money(f(L.net_income_owners)), f(L.net_income_owners), "KRW")
    if pre:
        r_rev, r_op = pre.rows.get("revenue"), pre.rows.get("operating_income")
        rev_qoq, rev_yoy = (r_rev.qoq, r_rev.yoy) if r_rev else (None, None)
        op_qoq, op_yoy = (r_op.qoq, r_op.yoy) if r_op else (None, None)
        if r_op and r_op.cum is not None:
            F.add("op_cum", lk, f"{L.year}년 누적 영업이익({pre.q}분기까지)", fmt.money(f(r_op.cum)), f(r_op.cum), "KRW", source=srcL)
            F.add("op_cum_yoy", lk, "누적 영업이익 전년 대비(회사 공시)", fmt.spct(r_op.cum_yoy), r_op.cum_yoy, "%")
    else:
        rev_qoq = _growth(rev, f(P.revenue) if P else None)
        rev_yoy = _growth(rev, f(Y.revenue) if Y else None)
        op_qoq = _growth(op, f(P.operating_income) if P else None)
        op_yoy = _growth(op, f(Y.operating_income) if Y else None)
    gsrc = "회사 공시 증감률" if pre else "분기 단독 기준 계산"
    F.add("rev_qoq", lk, f"매출액 전분기 대비({gsrc})", fmt.spct(rev_qoq), rev_qoq, "%")
    F.add("rev_yoy", lk, f"매출액 전년 동기 대비({gsrc})", fmt.spct(rev_yoy), rev_yoy, "%")
    F.add("op_qoq", lk, f"영업이익 전분기 대비({gsrc})", fmt.spct(op_qoq), op_qoq, "%")
    F.add("op_yoy", lk, f"영업이익 전년 동기 대비({gsrc})", fmt.spct(op_yoy), op_yoy, "%")
    c["op_yoy_L"] = op_yoy
    rows_t = []
    heads = [lab.replace(" 잠정", "P")]
    cons = c["inp"].consensus if pre else None
    if cons and cons.period != L.key.rstrip("P"):
        cons = None
    if cons:
        heads += ["컨센서스", "차이"]
    heads += [P.key if P else "직전 분기", "QoQ", Y.key if Y else "전년 동기", "YoY"]
    p_rev, p_op = (f(P.revenue), f(P.operating_income)) if P else (None, None)
    y_rev, y_op = (f(Y.revenue), f(Y.operating_income)) if Y else (None, None)
    p_opm = p_op / p_rev * 100 if p_rev and p_op is not None else None
    y_opm = y_op / y_rev * 100 if y_rev and y_op is not None else None
    if P:
        F.add("rev", P.key, f"매출액({P.key})", fmt.money(p_rev), p_rev, "KRW")
        F.add("op", P.key, f"영업이익({P.key})", fmt.money(p_op), p_op, "KRW")
        F.add("opm", P.key, f"영업이익률({P.key})", fmt.pct(p_opm), p_opm, "%")
    if Y:
        F.add("op", Y.key, f"영업이익({Y.key})", fmt.money(y_op), y_op, "KRW")
        F.add("opm", Y.key, f"영업이익률({Y.key})", fmt.pct(y_opm), y_opm, "%")
        if op and y_op and y_op > 0 and op / y_op >= 2:
            F.add("op_multiple", lk, f"영업이익 전년 동기 대비 배수({lk}/{Y.key})", fmt.times(op / y_op), op / y_op, "x")
            c["op_multiple"] = op / y_op
    if opm is not None and p_opm is not None:
        F.add("opm_qoq", lk, "영업이익률 전분기 대비 변화", fmt.pp(opm - p_opm), opm - p_opm, "%p")
    if opm is not None and y_opm is not None:
        F.add("opm_yoy", lk, "영업이익률 전년 동기 대비 변화", fmt.pp(opm - y_opm), opm - y_opm, "%p")
    div = data.table_div
    cons_cells = {}
    if cons:
        c_rev, c_op = cons.revenue, cons.operating_income
        c_opm = c_op / c_rev * 100 if c_rev and c_op else None
        who = f"{cons.provider} 컨센서스" if cons.provider else "컨센서스"
        src = f"{cons.outlet} 보도" if cons.outlet else None
        if c_rev:
            F.add("cons_rev", lk, f"{who} 매출액({cons.period})", fmt.money(c_rev), c_rev, "KRW", source=src)
            F.add("rev_vs_cons", lk, "매출액 컨센서스 대비", fmt.vs((rev / c_rev - 1) * 100), (rev / c_rev - 1) * 100, "%")
        if c_op:
            F.add("cons_op", lk, f"{who} 영업이익({cons.period})", fmt.money(c_op), c_op, "KRW", source=src)
            F.add("op_vs_cons", lk, "영업이익 컨센서스 대비", fmt.vs((op / c_op - 1) * 100), (op / c_op - 1) * 100, "%")
        if c_opm is not None and opm is not None:
            F.add("cons_opm", lk, "컨센서스 영업이익률", fmt.pct(c_opm), c_opm, "%")
            F.add("opm_vs_cons", lk, "영업이익률 컨센서스 대비", fmt.pp(opm - c_opm), opm - c_opm, "%p")
        cons_cells = {"rev": (fmt.tbl(c_rev, div), _sgn_cell((rev / c_rev - 1) * 100 if c_rev else None)),
                      "op": (fmt.tbl(c_op, div), _sgn_cell((op / c_op - 1) * 100 if c_op else None)),
                      "opm": (fmt.num(c_opm), _sgn_cell(opm - c_opm if c_opm is not None and opm is not None else None, "%p"))}
        c["cons"] = {"op": c_op, "rev": c_rev}

    def row(head, cur, cc, pv, g1, yv, g2):
        cells = [cur] + (list(cc) if cons else []) + [pv, g1, yv, g2]
        return {"head": head, "cells": cells}
    rows_t.append(row("매출액", fmt.tbl(rev, div), cons_cells.get("rev", ()), fmt.tbl(p_rev, div), fmt.signed(rev_qoq),
                      fmt.tbl(y_rev, div), fmt.signed(rev_yoy)))
    rows_t.append(row("영업이익", fmt.tbl(op, div), cons_cells.get("op", ()), fmt.tbl(p_op, div), fmt.signed(op_qoq),
                      fmt.tbl(y_op, div), fmt.signed(op_yoy)))
    rows_t.append(row("영업이익률", fmt.num(opm), cons_cells.get("opm", ()), fmt.num(p_opm),
                      fmt.pp(opm - p_opm) if opm is not None and p_opm is not None else "–", fmt.num(y_opm),
                      fmt.pp(opm - y_opm) if opm is not None and y_opm is not None else "–"))
    tips = {"QoQ": "회사 공시 증감률" if pre else "분기 단독 기준", "YoY": "회사 공시 증감률" if pre else "분기 단독 기준"}
    if cons:
        tips["컨센서스"] = f"{cons.provider or ''} 집계({cons.outlet} 보도)".strip()
    srcs = [c["name"]]
    if cons and cons.outlet:
        srcs.append(f"{cons.provider or '컨센서스'}({cons.outlet} 보도)")
    data.tables["latest"] = {"title": f"{pre.key} 잠정실적" if pre else f"{L.key} 실적", "unit": f"{data.table_unit}, %",
                             "heads": heads, "focal": 0, "tips": tips, "rows": rows_t, "source": ", ".join(srcs)}


def _sgn_cell(x: float | None, unit: str = "") -> dict:
    return {"v": fmt.signed(x, 1, unit), "sign": x}


# ---------------- 분기 영업이익(실적 절 첫 차트)
def _op_chart(c: dict) -> None:
    data, rows, L = c["data"], c["rows"], c["L"]
    pts = [(r.key, f(r.operating_income)) for r in rows if r.operating_income is not None][-12:]
    if len(pts) < 4 or L is None or pts[-1][0] != L.key:
        return
    cons = (c.get("cons") or {}).get("op")
    lab = data.latest["label"]
    mult = c.get("op_multiple")
    cur, yago = pts[-1][1], next((v for k, v in pts if k == qkey(L.year - 1, L.q)), None)
    if mult:
        title = f"분기 영업이익, 1년 만에 {mult:.1f}배"
    elif yago is not None and cur is not None and yago > 0 and cur > 0:
        g = (cur / yago - 1) * 100
        title = f"분기 영업이익, 1년 전보다 {abs(g):.0f}% {'늘었다' if g >= 0 else '줄었다'}"
    elif cur is not None and cur < 0:
        title = f"{L.key.rstrip('P')} 영업손실 {fmt.money_abs(cur)}"
    else:
        title = "분기 영업이익"
    legend = [["이전 분기", "bar", "tint"], [lab, "bar", "acc"]]
    if cons:
        legend.append([f"{L.key.rstrip('P')} 컨센서스", "line", "ink"])
    data.charts["op"] = {"keys": [k for k, _ in pts], "vals": [v for _, v in pts], "cons": cons, "label": lab,
                         "title": title, "subtitle": f"분기 영업이익, {_unit_word(data)}", "legend": legend,
                         "source": data.tables.get("latest", {}).get("source", c["name"])}


# ---------------- 영업이익률 이력(대표 차트)
def _opm_history(c: dict) -> None:
    F, data, rows, L = c["F"], c["data"], c["rows"], c["L"]
    ser = [(r.key, f(r.operating_income) / f(r.revenue) * 100) for r in rows
           if r.revenue and r.operating_income is not None and f(r.revenue) > 0]
    if len(ser) < 8 or c["inp"].industry == "financial":
        _ni_hero(c)
        return
    keys, vals = [k for k, _ in ser], [v for _, v in ser]
    cur = vals[-1]
    rank = 1 + sum(1 for v in vals[:-1] if v > cur)
    med = st.median(vals)
    hist = vals[:-1]
    t_idx = min(range(len(hist)), key=lambda i: hist[i])
    peak_idx = max(range(t_idx), key=lambda i: hist[i]) if t_idx > 0 else None
    first_year = "20" + keys[0][2:4]
    lk = keys[-1]
    F.add("opm_rank", "hist", f"{first_year}년 이후 분기 영업이익률 순위", f"{len(vals)}개 분기 중 {rank}위" if rank > 1 else f"{len(vals)}개 분기 중 최고",
          rank, "n", formula=f"{keys[0]}~{lk} {len(vals)}개 분기")
    F.add("opm_n", "hist", "영업이익률 비교 분기 수", f"{len(vals)}개 분기", len(vals), "n")
    F.add("opm_median", "hist", f"{first_year}년 이후 분기 영업이익률 중앙값", fmt.pct(med), med, "%")
    F.add("opm_trough", "hist", f"이전 저점 영업이익률({keys[t_idx]})", fmt.pct(hist[t_idx]), hist[t_idx], "%")
    F.add("opm_trough_q", "hist", "이전 저점 분기", keys[t_idx])
    if peak_idx is not None:
        F.add("opm_prev_peak", "hist", f"직전 사이클 고점 영업이익률({keys[peak_idx]})", fmt.pct(hist[peak_idx]), hist[peak_idx], "%")
        F.add("opm_prev_peak_q", "hist", "직전 사이클 고점 분기", keys[peak_idx])
        if hist[peak_idx] > 0:
            F.add("opm_vs_prev_peak", "hist", "현재 영업이익률 ÷ 직전 고점", fmt.times(cur / hist[peak_idx]), cur / hist[peak_idx], "x")
    title = (f"영업이익률이 {first_year}년 이후 가장 높다" if rank == 1
             else f"영업이익률 {cur:.1f}%, {first_year}년 이후 {len(vals)}개 분기 중 {rank}위")
    data.charts["hero"] = {"kind": "opm", "keys": keys, "vals": vals, "median": med,
                           "peak": [keys[peak_idx], hist[peak_idx]] if peak_idx is not None else None,
                           "current_label": data.latest["label"], "title": title, "subtitle": "분기 영업이익률, %",
                           "source": c["name"]}
    c["opm_first_year"] = first_year


def _ni_hero(c: dict) -> None:
    """영업이익률을 쓸 수 없을 때(금융업, 매출 계정 없음, 이력 부족): 분기 지배주주 순이익."""
    data, rows = c["data"], c["rows"]
    pts = [(r.key, f(r.net_income_owners if r.net_income_owners is not None else r.net_income)) for r in rows]
    pts = [(k, v) for k, v in pts if v is not None][-16:]
    if len(pts) < 4:
        return
    keys, vals = [k for k, _ in pts], [v for _, v in pts]
    hist = vals[:-1]
    rank = 1 + sum(1 for v in hist if v > vals[-1])
    first = "20" + keys[0][2:4]
    title = (f"분기 순이익이 {first}년 이후 가장 많다" if rank == 1 else
             f"분기 순이익 {fmt.money(vals[-1])}, {len(vals)}개 분기 중 {rank}위")
    data.charts["hero"] = {"kind": "ni", "keys": keys, "vals": vals, "label": data.latest["label"], "title": title,
                           "subtitle": f"분기 지배주주 순이익, {_unit_word(data)}", "source": c["name"]}


# ---------------- 연간 표
def _annual(c: dict) -> None:
    inp, F, data, qs, by = c["inp"], c["F"], c["data"], c["qs"], c["by"]
    fy_last = inp.filing.bsns_year - (0 if inp.filing.reprt_code == "11011" else 1)
    years = [y for y in range(fy_last - 2, fy_last + 1) if (y, 4) in inp.cum]
    px = inp.px
    cols, heads = [], []
    eq = {(r.year, r.q): f(r.equity_owners) for r in qs}
    for y in years:
        cu = inp.cum[(y, 4)]
        rev, op = f(cu.get("revenue")), None
        r4 = [by.get(qkey(y, q)) for q in (1, 2, 3, 4)]
        op = sum(f(r.operating_income) for r in r4) if all(r and r.operating_income is not None for r in r4) else None
        ni = sum(f(r.net_income_owners) for r in r4) if all(r and r.net_income_owners is not None for r in r4) else None
        sh = _shares_at(inp, (y, 4))
        dv = inp.dividends.get(y)
        eps = dv.eps if dv and dv.eps else (ni / sh if ni is not None and sh else None)
        e = eq.get((y, 4))
        e0 = eq.get((y - 1, 4))
        bps = e / sh if e and sh else None
        yend = close_on_or_before(px, date(y, 12, 31)) if px else None
        price = yend[1] if yend and yend[0].year == y else None
        cols.append({"rev": rev, "op": op, "ni": ni, "opm": op / rev * 100 if rev and op is not None else None,
                     "eps": eps, "bps": bps, "per": price / eps if price and eps and eps > 0 else None,
                     "pbr": price / bps if price and bps else None,
                     "roe": ni / ((e + e0) / 2) * 100 if ni is not None and e and e0 else None,
                     "dps": dv.dps_common if dv else None})
        heads.append(str(y))
    # 최근 4개 분기와 최근 분기×4
    if len(qs) >= 5:
        last4 = qs[-4:]
        ttm = {k: sum(f(getattr(r, k)) or 0 for r in last4) for k in ("revenue", "operating_income", "net_income_owners")}
        lastq = qs[-1]
        sh_now = _shares_at(inp, (lastq.year, lastq.q))
        e_now, e_prev = f(lastq.equity_owners), f(qs[-5].equity_owners)
        price = c.get("last_px")
        eps_ttm = ttm["net_income_owners"] / sh_now if sh_now else None
        bps_now = e_now / sh_now if e_now and sh_now else None
        roe_ttm = ttm["net_income_owners"] / ((e_now + e_prev) / 2) * 100 if e_now and e_prev else None
        ttm_label = f"{last4[0].key}–{last4[-1].key}"
        cols.append({"rev": ttm["revenue"], "op": ttm["operating_income"], "ni": ttm["net_income_owners"],
                     "opm": ttm["operating_income"] / ttm["revenue"] * 100 if ttm["revenue"] else None,
                     "eps": eps_ttm, "bps": bps_now, "per": price / eps_ttm if price and eps_ttm and eps_ttm > 0 else None,
                     "pbr": price / bps_now if price and bps_now else None, "roe": roe_ttm, "dps": None})
        heads.append(ttm_label)
        rr_ni = f(lastq.net_income_owners) * 4 if lastq.net_income_owners is not None else None
        eps_rr = rr_ni / sh_now if rr_ni is not None and sh_now else None
        rr_label = f"{lastq.key}×4"
        cols.append({"rev": f(lastq.revenue) * 4 if lastq.revenue else None,
                     "op": f(lastq.operating_income) * 4 if lastq.operating_income is not None else None, "ni": rr_ni,
                     "opm": f(lastq.operating_income) / f(lastq.revenue) * 100 if lastq.revenue and lastq.operating_income is not None else None,
                     "eps": eps_rr, "bps": bps_now, "per": price / eps_rr if price and eps_rr and eps_rr > 0 else None,
                     "pbr": price / bps_now if price and bps_now else None,
                     "roe": rr_ni / e_now * 100 if rr_ni is not None and e_now else None, "dps": None})
        heads.append(rr_label)
        c.update(ttm_label=ttm_label, rr_label=rr_label, eps_ttm=eps_ttm, bps_now=bps_now, roe_ttm=roe_ttm,
                 per_ttm=cols[-2]["per"], per_rr=cols[-1]["per"], pbr_now=cols[-2]["pbr"], ni_ttm=ttm["net_income_owners"],
                 rr_ni=rr_ni)
        F.add("ni_ttm", "ttm", f"최근 4개 분기({ttm_label}) 지배주주 순이익", fmt.money(ttm["net_income_owners"]), ttm["net_income_owners"], "KRW")
        F.add("per", "ttm", f"PER(최근 4개 분기 이익, {fmt.md(c.get('last_d', inp.today))} 종가)", fmt.times(cols[-2]["per"]), cols[-2]["per"], "x")
        F.add("per", "rr", f"PER({rr_label} 지배순이익 기준, 단순 연환산)", fmt.times(cols[-1]["per"]), cols[-1]["per"], "x")
        F.add("pbr", "now", "PBR(최근 분기말 지배주주 자본 기준)", fmt.times(cols[-2]["pbr"], 2), cols[-2]["pbr"], "x")
        F.add("roe", "ttm", "ROE(최근 4개 분기, 평균 자본)", fmt.pct(roe_ttm), roe_ttm, "%")
        F.add("ni", lastq.key, f"지배주주 순이익({lastq.key})", fmt.money(f(lastq.net_income_owners)), f(lastq.net_income_owners), "KRW")
        if len(qs) >= 4:
            q0 = last4[0]
            F.add("ni", q0.key, f"지배주주 순이익({q0.key})", fmt.money(f(q0.net_income_owners)), f(q0.net_income_owners), "KRW")
        data.key_data += [
            {"label": "PER", "value": fmt.times(cols[-2]["per"]), "sub": f"{rr_label} 기준 {fmt.times(cols[-1]['per'])}"},
        ]
    div = data.table_div
    focal = len(years) if len(cols) > len(years) else None
    R = lambda k, fn: [fn(col[k]) for col in cols]
    tips = {}
    if "ttm_label" in c:
        tips[c["ttm_label"]] = "최근 4개 분기 합계, 밸류에이션은 최근 종가"
        tips[c["rr_label"]] = f"{c['rr_label'][:-2]} 실적을 4배 한 값, 추정치 아님"
    data.tables["annual"] = {
        "title": "연간 실적과 밸류에이션", "unit": f"{data.table_unit}, 원, 배, %", "heads": heads, "focal": focal, "tips": tips,
        "rows": [
            {"head": "매출액", "cells": R("rev", lambda v: fmt.tbl(v, div))},
            {"head": "영업이익", "cells": R("op", lambda v: fmt.tbl(v, div))},
            {"head": "지배순이익", "cells": R("ni", lambda v: fmt.tbl(v, div))},
            {"head": "영업이익률", "cells": R("opm", fmt.num)},
            {"head": "EPS", "cells": R("eps", lambda v: fmt.num(v, 0)), "tip": "연도별은 회사 공시값, 최근은 보통+우선 유통주식수 기준"},
            {"head": "BPS", "cells": R("bps", lambda v: fmt.num(v, 0)), "tip": "지배주주 자본 ÷ 보통+우선 유통주식수"},
            {"head": "PER", "cells": R("per", fmt.num), "tip": "보통주 주가 ÷ EPS, 연도별은 연말 종가"},
            {"head": "PBR", "cells": R("pbr", fmt.num)},
            {"head": "ROE", "cells": R("roe", fmt.num), "tip": "지배주주 순이익 ÷ 평균 지배주주 자본"},
            {"head": "주당배당금", "cells": R("dps", lambda v: fmt.num(v, 0) if v else "–")},
        ], "source": f"{c['name']}, Yahoo Finance"}


def _split_factor(inp: Inputs, d: date) -> float:
    k = 1.0
    for sd, ratio in inp.splits:
        if sd > d and ratio > 0:
            k *= ratio
    return k


def _shares_at(inp: Inputs, yq: tuple[int, int]) -> float | None:
    """그 분기말 기준 보통+우선 유통주식수(분할 환산). 분기말 주식수가 없으면 직전 사업연도 말 값을 쓴다."""
    cands = [k for k in inp.shares if k <= yq]
    if not cands:
        cands = sorted(inp.shares)[:1]
        if not cands:
            return None
    k = max(cands)
    sh = inp.shares[k]
    y, q = k
    end = date(y, q * 3, 28)
    return sh.total_out * _split_factor(inp, end)


# ---------------- 분기·부문 표
def _quarter_table(c: dict) -> None:
    data, rows = c["data"], c["rows"]
    keys = [r.key for r in rows][-8:]
    by = {r.key: r for r in rows}
    div = data.table_div
    segq = c.get("segq")  # _segments가 뒤에 돌기 때문에 표는 _segments에서 마무리한다
    c["qt_keys"] = keys
    data.tables["quarters"] = {"title": "분기 실적과 부문별 실적", "unit": f"{data.table_unit}, %", "heads": keys,
                               "focal": len(keys) - 1 if by[keys[-1]].preliminary else None, "tips": {},
                               "rows": [{"head": "매출액", "cells": [fmt.tbl(f(by[k].revenue), div) for k in keys]},
                                        {"head": "영업이익", "cells": [fmt.tbl(f(by[k].operating_income), div) for k in keys]},
                                        {"head": "영업이익률", "cells": [fmt.num(f(by[k].operating_income) / f(by[k].revenue) * 100)
                                                                     if by[k].revenue and by[k].operating_income is not None else "–" for k in keys]},
                                        {"head": "지배순이익", "cells": [fmt.tbl(f(by[k].net_income_owners), div) for k in keys]}],
                               "source": c["name"]}
    del segq


def _segments(c: dict) -> None:
    inp, F, data, rows = c["inp"], c["F"], c["data"], c["rows"]
    cum = inp.segments
    if not cum:
        return
    names = None
    for v in cum.values():
        names = set(v) if names is None else names & set(v)
    if not names or len(names) < 2:
        data.notes.append("부문: 기간마다 부문 이름이 달라 분기 값을 만들 수 없다")
        return
    segq: dict[str, dict[str, dict[str, float]]] = {}
    for (y, q), v in sorted(cum.items()):
        base = cum.get((y, q - 1)) if q > 1 else {}
        if q > 1 and base is None:
            continue
        segq[qkey(y, q)] = {n: {k: float(v[n][k] - (base[n][k] if q > 1 else 0)) for k in ("rev", "op")} for n in names}
    keys = [k for k in [r.key for r in rows] if k in segq][-8:]
    if len(keys) < 4:
        return
    last = keys[-1]
    seg_last = segq[last]
    main = max(names, key=lambda n: seg_last[n]["op"])
    rest = [n for n in names if n != main]
    second = max(rest, key=lambda n: seg_last[n]["rev"])
    others = sorted((n for n in rest if n != second), key=lambda n: -seg_last[n]["op"])
    short = lambda n: re.sub(r"\s*부문$", "", n)
    total_op = f(next(r for r in rows if r.key == last).operating_income)
    share = seg_last[main]["op"] / total_op * 100 if total_op and total_op > 0 else None
    yago = qkey(int("20" + last[2:4]) - 1, int(last[0]))
    lk = per_label(last)
    F.add("seg1_op", last, f"{short(main)} 부문 영업이익({last})", fmt.money(seg_last[main]["op"]), seg_last[main]["op"], "KRW",
          source="정기보고서 사업부문별 요약 재무 현황(내부거래 포함)")
    if share is not None:
        F.add("seg1_share", last, f"{short(main)} 부문 영업이익 ÷ 전사 영업이익({last})", fmt.pct(share), share, "%")
    if seg_last[main]["rev"]:
        F.add("seg1_opm", last, f"{short(main)} 부문 영업이익률({last})", fmt.pct(seg_last[main]["op"] / seg_last[main]["rev"] * 100),
              seg_last[main]["op"] / seg_last[main]["rev"] * 100, "%")
    if yago in segq and segq[yago][main]["rev"]:
        v = segq[yago][main]["op"] / segq[yago][main]["rev"] * 100
        F.add("seg1_opm", yago, f"{short(main)} 부문 영업이익률({yago})", fmt.pct(v), v, "%")
    s2 = seg_last[second]
    F.add("seg2_op", last, f"{short(second)} 부문 영업이익({last})", fmt.money(s2["op"]), s2["op"], "KRW")
    if s2["op"] < 0:
        F.add("seg2_loss", last, f"{short(second)} 부문 영업손실({last})", fmt.money_abs(s2["op"]), -s2["op"], "KRW")
        first_loss = all(segq[k][second]["op"] >= 0 for k in keys[:-1])
        if first_loss:
            F.add("seg2_first_loss", last, f"{short(second)} 부문 적자 여부(최근 {len(keys)}개 분기)", f"최근 {len(keys)}개 분기 중 처음")
    if yago in segq and segq[yago][second]["rev"] > 0:
        g = (s2["rev"] / segq[yago][second]["rev"] - 1) * 100
        F.add("seg2_rev_yoy", last, f"{short(second)} 부문 매출 전년 동기 대비({last})", fmt.change(g), g, "%")
    for i, n in enumerate(sorted(others, key=lambda n: -seg_last[n]["op"]), 3):
        F.add(f"seg{i}_op", last, f"{short(n)} 부문 영업이익({last})", fmt.money(seg_last[n]["op"]), seg_last[n]["op"], "KRW")
    series = [(short(main), "acc", [segq[k][main]["op"] for k in keys]),
              (short(second), "mid", [segq[k][second]["op"] for k in keys])]
    if others:
        series.append(("·".join(short(n) for n in others), "light", [sum(segq[k][n]["op"] for n in others) for k in keys]))
    grew = segq[keys[0]][main]["op"] < seg_last[main]["op"]
    if s2["op"] < 0 and grew:
        title = f"{short(main)} 이익이 커지는 동안 {short(second)}는 적자로"
    elif s2["op"] < 0:
        title = f"{short(second)}는 적자, 영업이익의 {share:.0f}%가 {short(main)}" if share else f"{short(second)}는 적자"
    elif share:
        title = f"영업이익의 {share:.1f}%가 {short(main)}"
    else:
        title = "부문별 영업이익"
    data.charts["segments"] = {"keys": keys, "series": series, "title": title, "subtitle": f"부문별 분기 영업이익, {_unit_word(data)}",
                               "focal_label": f"{short(main)} {_u(seg_last[main]['op'], data)}",
                               "neg_label": f"{short(second)} {fmt.money_abs(s2['op'])} 적자" if s2["op"] < 0 else None,
                               "source": c["name"]}
    # 분기 표에 부문 줄 끼우기
    t = data.tables.get("quarters")
    if t:
        qk = t["heads"]
        div = data.table_div
        segrow = lambda n, k: [fmt.tbl(segq[q][n][k], div) if q in segq else "–" for q in qk]
        rev_sub = [{"head": short(n), "cells": segrow(n, "rev"), "sub": True, "tip": "부문 간 내부거래 포함"} for n in (main, second)]
        op_sub = [{"head": short(n), "cells": segrow(n, "op"), "sub": True, "tip": "부문 간 내부거래 포함"} for n in [main, second] + others]
        t["rows"] = [t["rows"][0]] + rev_sub + [t["rows"][1]] + op_sub + t["rows"][2:]
    c["segq"] = segq


def _unit_word(data: ReportData) -> str:
    return "조원" if data.table_div == 1e9 else "억원"


def _u(v: float, data: ReportData) -> str:
    return f"{v / 1e12:,.1f}" if data.table_div == 1e9 else f"{v / 1e8:,.0f}"


# ---------------- 밸류에이션(PBR 밴드·PER)
def _valuation(c: dict) -> None:
    inp, F, data, qs = c["inp"], c["F"], c["data"], c["qs"]
    px = inp.px
    if not px or len(qs) < 8:
        return
    pts = []   # (가용일, bps, eps_ttm)
    for i, r in enumerate(qs):
        sh = _shares_at(inp, (r.year, r.q))
        if not sh or r.equity_owners is None or not r.rcept_no:
            continue
        avail = date(int(r.rcept_no[:4]), int(r.rcept_no[4:6]), int(r.rcept_no[6:8]))
        last4 = qs[max(0, i - 3):i + 1]
        eps = (sum(f(x.net_income_owners) for x in last4) / sh
               if len(last4) == 4 and all(x.net_income_owners is not None for x in last4) else None)
        pts.append((avail, f(r.equity_owners) / sh, eps))
    pts.sort()
    if not pts:
        return
    months: dict[tuple[int, int], tuple[date, float]] = {}
    for d, v in px:
        months[(d.year, d.month)] = (d, v)
    band = []
    avail_dates = [p[0] for p in pts]
    for (yy, mm), (d, v) in sorted(months.items()):
        i = bisect_right(avail_dates, d) - 1
        if i < 0:
            continue
        _, bps, eps = pts[i]
        band.append({"d": d.isoformat(), "p": v, "bps": bps, "pbr": v / bps, "per": v / eps if eps and eps > 0 else None})
    if len(band) < 24:
        return
    cur = band[-1]
    pbrs = sorted(b["pbr"] for b in band)
    pers = sorted(b["per"] for b in band if b["per"])
    pct_le = sum(1 for x in pbrs if x <= cur["pbr"]) / len(pbrs) * 100
    bmin, bmax = min(band, key=lambda b: b["pbr"]), max(band, key=lambda b: b["pbr"])
    first_year = band[0]["d"][:4]
    top = 100 - round(pct_le)
    pos_txt = f"상위 {top}%" if pct_le >= 50 else f"하위 {round(pct_le)}%"
    F.add("pbr_pos", "hist", f"PBR 위치({first_year}년 이후 월말 {len(band)}개월 중)", pos_txt, pct_le, "%")
    F.add("pbr_median", "hist", f"{first_year}년 이후 월말 PBR 중앙값", fmt.times(st.median(pbrs), 2), st.median(pbrs), "x")
    F.add("pbr_min", "hist", f"{first_year}년 이후 월말 PBR 최저({fmt.ym(bmin['d'])})", fmt.times(bmin["pbr"], 2), bmin["pbr"], "x")
    F.add("pbr_min_ym", "hist", "PBR 최저 시점", fmt.ym(bmin["d"]))
    F.add("pbr_max", "hist", f"{first_year}년 이후 월말 PBR 최고({fmt.ym(bmax['d'])})", fmt.times(bmax["pbr"], 2), bmax["pbr"], "x")
    F.add("pbr_max_ym", "hist", "PBR 최고 시점", fmt.ym(bmax["d"]))
    F.add("band_months", "hist", "밴드 비교 개월 수", f"{len(band)}개월", len(band), "n")
    F.add("band_start", "hist", "밴드 시작", fmt.ym(band[0]["d"]))
    if pers and c.get("per_ttm"):
        per_pct = sum(1 for x in pers if x <= c["per_ttm"]) / len(pers) * 100
        F.add("per_median", "hist", f"{first_year}년 이후 월말 PER 중앙값(최근 4개 분기 이익)", fmt.times(st.median(pers)), st.median(pers), "x")
        F.add("per_pct", "hist", "현재 PER 백분위", f"{round(per_pct)}%", per_pct, "%")
    if bmax is not cur and band.index(bmax) >= len(band) - 24:
        F.add("px_since_peak", "hist", f"PBR 최고 시점({fmt.ym(bmax['d'])}) 이후 주가 변화", fmt.move((cur["p"] / bmax["p"] - 1) * 100),
              (cur["p"] / bmax["p"] - 1) * 100, "%")
        F.add("bps_since_peak", "hist", f"같은 기간 BPS 변화", fmt.change((cur["bps"] / bmax["bps"] - 1) * 100),
              (cur["bps"] / bmax["bps"] - 1) * 100, "%")
        F.add("pbr_since_peak", "hist", f"같은 기간 PBR 변화", fmt.move((cur["pbr"] / bmax["pbr"] - 1) * 100, 0),
              (cur["pbr"] / bmax["pbr"] - 1) * 100, "%")
        title = f"PBR은 {bmin['pbr']:.2f}배에서 {bmax['pbr']:.2f}배까지 오른 뒤 {cur['pbr']:.2f}배" if band.index(bmin) < band.index(bmax) \
            else f"PBR {cur['pbr']:.2f}배, {first_year}년 이후 {pos_txt}"
    else:
        title = f"PBR {cur['pbr']:.2f}배, {first_year}년 이후 {pos_txt}"
    mx = max(max(b["p"] for b in band), max(b["bps"] for b in band) * bmax["pbr"])
    step = _nice_step(bmax["pbr"] / 4)
    mults = [round(step * i, 2) for i in range(1, 5)]
    data.charts["band"] = {"rows": [[b["d"], b["p"], b["bps"]] for b in band], "mults": mults,
                           "min": band.index(bmin), "max": band.index(bmax), "title": title,
                           "subtitle": "월말 주가와 PBR 배수별 주가, 천원" if mx > 1e4 else "월말 주가와 PBR 배수별 주가, 원",
                           "source": f"{c['name']}, Yahoo Finance"}
    c["pbr_pos_txt"] = pos_txt
    data.key_data.append({"label": "PBR", "value": fmt.times(cur["pbr"], 2), "sub": f"{first_year}년 이후 {pos_txt}"})


def _nice_step(x: float) -> float:
    for s in (0.25, 0.5, 1, 2, 2.5, 5, 10):
        if s >= x * 0.85:
            return s
    return 10.0


# ---------------- 피어
def _peers(c: dict) -> None:
    inp, F, data = c["inp"], c["F"], c["data"]
    rows = [r for r in inp.peers if r.mcap]
    if len(rows) < 3:
        return
    me = next((r for r in rows if r.is_self), None)
    others = [r for r in rows if not r.is_self]
    if me is None or not others:
        return
    top = max(others, key=lambda r: r.mcap or 0)
    nm = clean_name(top.name)
    F.add("roe", "self", f"{c['name']} ROE(최근 4개 분기)", fmt.pct(me.roe), me.roe, "%")
    F.add("roe", "peer1", f"{nm} ROE(최근 4개 분기)", fmt.pct(top.roe), top.roe, "%")
    F.add("pbr", "peer1", f"{nm} PBR", fmt.times(top.pbr, 2), top.pbr, "x")
    F.add("per", "peer1", f"{nm} PER(최근 4개 분기)", fmt.times(top.per), top.per, "x")
    if me.roe and top.roe and top.roe > 0:
        F.add("roe_ratio", "peer1", f"{c['name']} ROE ÷ {nm} ROE", fmt.fraction(me.roe / top.roe), me.roe / top.roe, "x")
    if me.pbr and top.pbr:
        F.add("pbr_ratio", "peer1", f"{c['name']} PBR ÷ {nm} PBR", fmt.fraction(me.pbr / top.pbr), me.pbr / top.pbr, "x")
    if top.per_rr:
        F.add("per", "peer1rr", f"{nm} PER(최근 분기 지배순이익×4 기준)", fmt.times(top.per_rr), top.per_rr, "x")
    div2 = (inp.corp.induty_code or "")[:2]
    same = bool(div2) and all((r.induty_code or "").startswith(div2) for r in rows)
    ind = industry_short(div2) if same else None
    group = f"{ind} 업종 시가총액 상위 {len(rows)}개사" if ind else f"업종이 비슷한 시가총액 상위 {len(rows)}개사"
    F.add("peer_group", "peer1", "비교 기업군", group)
    ttm = c.get("ttm_label", "최근 4개 분기")
    title = "시가총액 상위 동종 업종 비교"
    if me.roe and top.roe and me.pbr and top.pbr and me.roe < top.roe and me.pbr < top.pbr:
        title = f"ROE는 {nm}의 {fmt.fraction(me.roe / top.roe)}, PBR은 {fmt.fraction(me.pbr / top.pbr)}"
    data.charts["peers"] = {"rows": [[clean_name(r.name), r.roe, r.pbr, r.is_self] for r in rows if r.roe is not None and r.pbr],
                            "title": title, "subtitle": f"{group}, ROE({ttm})와 PBR", "source": "각 사, Yahoo Finance"}
    div = data.table_div

    def per_cell(r: PeerRow) -> str:
        if r.per is None:
            return "적자" if (r.ni_ttm or 0) < 0 else "–"
        return "n.m." if r.per > 200 else fmt.num(r.per)
    asof = fmt.md(c["last_d"]) if c.get("last_d") else "최근"
    data.tables["peers"] = {"title": group, "unit": f"{data.table_unit}, %, 배",
                            "heads": ["시가총액", "지배순이익", "ROE", "PBR", "PER"], "focal": None,
                            "tips": {"시가총액": "보통주·우선주 합산", "지배순이익": f"{ttm} 합계", "ROE": "평균 지배주주 자본 기준",
                                     "PBR": f"{asof} 보통주 종가 기준", "PER": f"{asof} 보통주 종가 ÷ EPS({ttm})"},
                            "rows": [{"head": clean_name(r.name), "bold": r.is_self,
                                      "cells": [fmt.tbl(r.mcap, div), fmt.tbl(r.ni_ttm, div), fmt.num(r.roe),
                                                fmt.num(r.pbr, 2), per_cell(r)]} for r in rows],
                            "source": "각 사, Yahoo Finance"}
    c["peer1"] = nm


# ---------------- 현금흐름·주주환원
def _cash(c: dict) -> None:
    inp, F, data, qs = c["inp"], c["F"], c["data"], c["qs"]
    y, q = inp.filing.bsns_year, _q_of(inp.filing.reprt_code)
    fy_last = y if q == 4 else y - 1
    cols = [(fy_last - 2, 4), (fy_last - 1, 4), (fy_last, 4)]
    if q < 4:
        cols += [(y - 1, q), (y, q)]
    cols = [k for k in cols if k in inp.cum]
    if len(cols) < 2:
        return
    lab = lambda k: str(k[0]) if k[1] == 4 else f"{k[0]} {['1분기', '상반기', '3분기'][k[1] - 1]}"
    plab = lambda k: f"FY{str(k[0])[2:]}" if k[1] == 4 else f"{['Q1', 'H1', 'M9'][k[1] - 1]}{str(k[0])[2:]}"

    def calc(k):
        r = inp.cum[k]
        g = lambda n: f(r.get(n))
        cfo, ni, capex, intan = g("cfo"), g("net_income"), g("capex"), g("intangible_capex")
        cash = sum(g(n) or 0 for n in CASH_LIKE)
        debt = sum(g(n) or 0 for n in DEBT_LIKE)
        fcf = cfo - abs(capex or 0) - abs(intan or 0) if cfo is not None else None
        sr = abs(g("buyback") or 0) + abs(g("dividends_paid") or 0)
        return {"cfo": cfo, "ni": ni, "cfo_ni": cfo / ni if cfo is not None and ni and ni > 0 else None,
                "ar": g("receivables"), "inv": g("inventory"), "capex": abs(capex) if capex is not None else None,
                "intan": abs(intan) if intan is not None else None,
                "capex_rev": abs(capex) / g("revenue") * 100 if capex is not None and g("revenue") else None,
                "fcf": fcf, "sr": sr, "buyback": abs(g("buyback") or 0), "div": abs(g("dividends_paid") or 0),
                "net_cash": cash - debt if cash else None}
    vals = {k: calc(k) for k in cols}
    cur = cols[-1]
    V = vals[cur]
    cl = plab(cur)
    lc = lab(cur)
    F.add("fcf", cl, f"잉여현금흐름({lc})", fmt.money(V["fcf"]), V["fcf"], "KRW", formula="영업활동현금흐름 − 유형·무형자산 취득")
    F.add("cfo", cl, f"영업활동현금흐름({lc})", fmt.money(V["cfo"]), V["cfo"], "KRW")
    F.add("capex", cl, f"유형자산 취득({lc})", fmt.money(V["capex"]), V["capex"], "KRW")
    F.add("intan", cl, f"무형자산 취득({lc})", fmt.money(V["intan"]), V["intan"], "KRW")
    F.add("net_cash", cl, f"순현금({lc} 말)", fmt.money(V["net_cash"]), V["net_cash"], "KRW", formula="현금성자산·단기금융상품 등 − 차입금·사채")
    F.add("cfo_ni", cl, f"영업현금흐름/순이익({lc})", fmt.times(V["cfo_ni"], 2), V["cfo_ni"], "x")
    F.add("capex_rev", cl, f"매출 대비 유형자산 취득({lc})", fmt.pct(V["capex_rev"]), V["capex_rev"], "%")
    F.add("sr", cl, f"자기주식 취득+배당 지급({lc})", fmt.money(V["sr"]), V["sr"], "KRW")
    F.add("buyback", cl, f"자기주식 취득 지급액({lc})", fmt.money(V["buyback"]), V["buyback"], "KRW")
    F.add("div_paid", cl, f"배당금 지급액({lc})", fmt.money(V["div"]), V["div"], "KRW")
    if V["fcf"] and V["fcf"] > 0 and V["sr"]:
        F.add("sr_share", cl, "주주환원 ÷ 잉여현금흐름", f"{V['sr'] / V['fcf'] * 100:.0f}%", V["sr"] / V["fcf"] * 100, "%")
    fy = (fy_last, 4)
    if fy in vals and fy != cur:
        W = vals[fy]
        fl = plab(fy)
        F.add("fcf", fl, f"잉여현금흐름({fy[0]}년)", fmt.money(W["fcf"]), W["fcf"], "KRW")
        F.add("net_cash", fl, f"순현금({fy[0]}년 말)", fmt.money(W["net_cash"]), W["net_cash"], "KRW")
        if W["fcf"] and W["fcf"] > 0 and V["fcf"]:
            F.add("fcf_vs_fy", cl, f"{lc} 잉여현금흐름 ÷ {fy[0]}년 연간", fmt.times(V["fcf"] / W["fcf"]), V["fcf"] / W["fcf"], "x")
        if V["net_cash"] is not None and W["net_cash"] is not None:
            d = V["net_cash"] - W["net_cash"]
            F.add("net_cash_chg", cl, f"순현금 변화({fy[0]}년 말 → {lc} 말)", fmt.amount_change(d), d, "KRW")
            c["net_cash_chg"] = d
        if V["ar"] is not None and W["ar"] is not None:
            F.add("ar_chg", cl, f"매출채권 변화({fy[0]}년 말 → {lc} 말)", fmt.amount_change(V["ar"] - W["ar"]), V["ar"] - W["ar"], "KRW")
        if V["inv"] is not None and W["inv"] is not None:
            F.add("inv_chg", cl, f"재고자산 변화({fy[0]}년 말 → {lc} 말)", fmt.amount_change(V["inv"] - W["inv"]), V["inv"] - W["inv"], "KRW")
    prev_same = (cur[0] - 1, cur[1])
    if prev_same in vals and cur[1] < 4:
        P = vals[prev_same]
        F.add("cfo_ni", plab(prev_same), f"영업현금흐름/순이익({lab(prev_same)})", fmt.times(P["cfo_ni"], 2), P["cfo_ni"], "x")
        if P["capex"] and V["capex"]:
            g = (V["capex"] / P["capex"] - 1) * 100
            F.add("capex_yoy", cl, "유형자산 취득 전년 동기 대비", fmt.change(g), g, "%")
    first = cols[0]
    F.add("capex_rev", plab(first), f"매출 대비 유형자산 취득({lab(first)})", fmt.pct(vals[first]["capex_rev"]), vals[first]["capex_rev"], "%")
    # 매출채권 회수 기간: 기말 매출채권 ÷ 마지막 분기 매출 × 일수
    by = c["by"]
    lastq = qs[-1]
    if V["ar"] and lastq.revenue:
        days = V["ar"] / f(lastq.revenue) * QDAYS[lastq.q]
        F.add("ar_days", lastq.key, f"매출채권 회수 기간({lastq.key}말, 분기 매출 기준)", f"{days:.0f}일", days, "n")
    if fy in vals and vals[fy]["ar"]:
        q4 = by.get(qkey(fy[0], 4))
        if q4 and q4.revenue:
            days = vals[fy]["ar"] / f(q4.revenue) * 92
            F.add("ar_days", q4.key, f"매출채권 회수 기간({q4.key}말)", f"{days:.0f}일", days, "n")
    # 발행주식수 감소(소각)
    base_y = fy_last - 1
    b, n_ = inp.shares.get((base_y, 4)), inp.shares.get(cur) or inp.shares.get((y, q))
    if b and n_:
        cc = (n_.common_issued / b.common_issued - 1) * 100
        if cc < -0.1:
            F.add("shares_cut_common", "since", f"{base_y}년 말 이후 보통주 발행주식수 변화", f"{abs(cc):.1f}% 감소", cc, "%")
        if b.pref_issued and n_.pref_issued:
            cp = (n_.pref_issued / b.pref_issued - 1) * 100
            if cp < -0.1:
                F.add("shares_cut_pref", "since", f"{base_y}년 말 이후 우선주 발행주식수 변화", f"{abs(cp):.1f}% 감소", cp, "%")
        F.add("shares_base_year", "since", "발행주식수 비교 기준 연도", f"{base_y}년 말")
    # 진행 중인 자기주식 취득 결정
    active = [bd for bd in inp.buybacks if bd.end and bd.end >= (inp.today - timedelta(days=30)).isoformat()]
    if active:
        bd = active[-1]
        F.add("bb_amount", "now", "자기주식 취득 결정 금액(보통주)", fmt.money(bd.amount_common), bd.amount_common, "KRW",
              source=f"주요사항보고서 rcpNo {bd.rcept_no}")
        F.add("bb_shares", "now", "자기주식 취득 예정 주식수(보통주)", f"{bd.shares_common:,}주", bd.shares_common, "n")
        F.add("bb_period", "now", "자기주식 취득 기간", f"{fmt.md(bd.start)}~{fmt.md(bd.end)}" if bd.start and bd.end else None)
        F.add("bb_end", "now", "자기주식 취득 종료일", fmt.md(bd.end) if bd.end else None)
        F.add("bb_decided", "now", "자기주식 취득 결정일", fmt.md(bd.decided) if bd.decided else None)
        F.add("bb_purpose", "now", "자기주식 취득 목적", bd.purpose)
        c["buyback"] = bd
    div = data.table_div
    R = lambda key, fn=lambda v: fmt.tbl(v, div): [fn(vals[k][key]) for k in cols]
    data.tables["cash"] = {"title": "현금흐름과 운전자본", "unit": f"{data.table_unit}, 배, %", "heads": [lab(k) for k in cols],
                           "focal": len(cols) - 1, "tips": {},
                           "rows": [{"head": "영업활동현금흐름", "cells": R("cfo")},
                                    {"head": "당기순이익", "cells": R("ni")},
                                    {"head": "영업현금흐름/순이익", "cells": R("cfo_ni", lambda v: fmt.num(v, 2))},
                                    {"head": "매출채권(기말)", "cells": R("ar")},
                                    {"head": "재고자산(기말)", "cells": R("inv")},
                                    {"head": "유형자산 취득", "cells": R("capex")},
                                    {"head": "유형자산 취득/매출", "cells": R("capex_rev", fmt.num)},
                                    {"head": "잉여현금흐름", "cells": R("fcf"), "tip": "영업활동현금흐름 − 유형·무형자산 취득"},
                                    {"head": "자기주식 취득+배당", "cells": R("sr"), "tip": "현금흐름표 지급액"},
                                    {"head": "순현금(기말)", "cells": R("net_cash"), "tip": "현금성자산·단기금융상품 등 − 차입금·사채"}],
                           "source": c["name"]}
    fy_cols = [k for k in cols if k[1] == 4] + ([cur] if cur[1] < 4 else [])
    if "net_cash_chg" in c and c["net_cash_chg"] > 0:
        span = {1: "석 달", 2: "반년", 3: "아홉 달", 4: "1년"}[cur[1]]
        title = f"순현금은 {span} 만에 {fmt.money(c['net_cash_chg'])} 늘었다"
    elif V["fcf"] and V["fcf"] > 0:
        title = f"{lc} 잉여현금흐름 {fmt.money(V['fcf'])}"
    else:
        title = "잉여현금흐름과 순현금"
    data.charts["cash"] = {"cols": [[lab(k), vals[k]["fcf"], vals[k]["net_cash"]] for k in fy_cols],
                           "title": title, "subtitle": f"잉여현금흐름과 기말 순현금, {_unit_word(data)}", "source": c["name"]}


# ---------------- 잠정실적 발표 반응
def _events(c: dict) -> None:
    inp, F, data, by_all = c["inp"], c["F"], c["data"], c["by_all"]
    px, ix = inp.px, inp.index_px
    if not px or not ix or not inp.prelim_firsts:
        return
    pd_ = {d: v for d, v in px}
    idd = {d: v for d, v in ix}
    days = [d for d, _ in px if d in idd]
    out = []
    for fl in inp.prelim_firsts:
        d0 = fl.rcept_dt
        if d0 not in pd_ or d0 not in idd:
            continue
        i = days.index(d0)
        if i == 0:
            continue
        dm1 = days[i - 1]
        r = lambda a, b, s: s[a] / s[b] - 1
        ex0 = (r(d0, dm1, pd_) - r(d0, dm1, idd)) * 100
        ex3 = (r(days[i + 2], dm1, pd_) - r(days[i + 2], dm1, idd)) * 100 if i + 2 < len(days) else None
        y, q = fl.quarter_guess
        k = qkey(y, q)
        row = by_all.get(k) or by_all.get(k + "P")
        prev = by_all.get(qkey(y - 1, q))
        op = f(row.operating_income) if row else None
        yoy = _growth(op, f(prev.operating_income) if prev else None)
        if row is not None and row.preliminary and c.get("op_yoy_L") is not None:
            yoy = c["op_yoy_L"]
        out.append({"date": d0.isoformat(), "q": k, "rcept_no": fl.rcept_no, "op": op, "op_yoy": yoy, "ex0": ex0, "ex3": ex3,
                    "day_stock": r(d0, dm1, pd_) * 100, "day_index": r(d0, dm1, idd) * 100})
    if len(out) < 4:
        return
    done = [e for e in out if e["ex3"] is not None]
    hist = [e for e in out[:-1]] if out[-1]["ex3"] is None else out
    F.add("ev_n", "all", f"{out[0]['date'][:4]}년 이후 잠정실적 발표 횟수(최근 제외)", f"{len(hist)}번", len(hist), "n")
    F.add("ev_abs_mean", "all", f"발표일 {inp.index_name} 대비 초과수익률 절댓값 평균", f"{st.mean(abs(e['ex0']) for e in hist):.1f}%p",
          st.mean(abs(e["ex0"]) for e in hist), "%p")
    last = out[-1]
    F.add("ev_ex0", "last", f"{last['q']} 발표일({fmt.md(last['date'])}) {inp.index_name} 대비 초과수익률", fmt.pp(last["ex0"]), last["ex0"], "%p")
    F.add("ev_day_stock", "last", f"{fmt.md(last['date'])} 주가 등락률", fmt.spct(last["day_stock"], 2), last["day_stock"], "%")
    F.add("ev_day_index", "last", f"{fmt.md(last['date'])} {inp.index_name} 등락률", fmt.spct(last["day_index"], 2), last["day_index"], "%")
    last3 = done[-3:]
    if len(last3) == 3:
        F.add("ev_last3_ex3", "all", "최근 세 번 발표 후 3거래일 초과수익률",
              ", ".join(fmt.pp(e["ex3"]) for e in last3) + f"({'·'.join(e['q'] for e in last3)})")
        if all(e["op_yoy"] and e["op_yoy"] > 200 for e in last3):
            F.add("ev_last3_growth", "all", "최근 세 번 발표의 영업이익 전년 대비 증가율", "모두 200% 이상")
    neg3 = len(last3) == 3 and all(e["ex3"] < 0 for e in last3)
    pos3 = len(last3) == 3 and all(e["ex3"] > 0 for e in last3)
    title = (f"최근 세 번의 발표 뒤 3거래일은 모두 {inp.index_name} 하회" if neg3 else
             f"최근 세 번의 발표 뒤 3거래일은 모두 {inp.index_name} 상회" if pos3 else
             f"잠정실적 발표 뒤 3거래일, {inp.index_name} 대비")
    data.charts["events"] = {"rows": [[e["q"], e["ex3"], e["date"], e["op_yoy"]] for e in done], "title": title,
                             "subtitle": f"잠정실적 발표일 포함 3거래일, {inp.index_name} 대비 초과수익률", "source": "DART, Yahoo Finance"}
    data.tables["events"] = out


# ---------------- 1년 주가
def _rel(c: dict) -> None:
    inp, F, data = c["inp"], c["F"], c["data"]
    px, ix = inp.px, inp.index_px
    if not px or not ix:
        return
    end = px[-1][0]
    idd = dict(ix)
    both = [(d, v) for d, v in px if d in idd]
    i0 = bisect_right([d for d, _ in both], end - timedelta(days=365)) - 1   # ret_between과 같은 기준일
    yr = both[max(i0, 0):]
    if len(yr) < 60:
        return
    s0, i0 = yr[0][1], idd[yr[0][0]]
    rows = [[d.isoformat(), v / s0 * 100, idd[d] / i0 * 100, v, idd[d]] for d, v in yr]
    rel = rows[-1][1] - rows[-1][2]
    ev = [e["date"] for e in data.tables.get("events", []) if e["date"] >= yr[0][0].isoformat()]
    title = (f"1년 동안 {inp.index_name}보다 {rel:.0f}%p 더 올랐다" if rel >= 0 else f"1년 동안 {inp.index_name}보다 {abs(rel):.0f}%p 덜 올랐다")
    data.charts["rel"] = {"rows": rows, "index": inp.index_name, "name": c["name"], "events": ev, "title": title,
                          "subtitle": f"{fmt.ym(yr[0][0])} {yr[0][0].day}일=100" + (", 원 표시는 잠정실적 발표일" if ev else ""),
                          "source": "Yahoo Finance"}
    tail = data.extra.get("key_tail")
    if tail:
        data.key_data.append({"label": "12개월 수익률", "value": fmt.spct(tail["r12"]),
                              "sub": f"{tail['index']} 대비 {fmt.pp(tail['rel12'])}", "sub_sign": tail["rel12"]})
        data.key_data.append({"label": "52주 최고", "value": fmt.won(tail["hi"]), "sub": f"최저 {fmt.won(tail['lo'])}"})


# ---------------- 뉴스·공시 목록
KEY_DISCLOSURE = ("잠정", "기업설명회", "자기주식", "배당", "합병", "분할", "유상증자", "무상증자", "전환사채", "신주인수권",
                  "공급계약", "투자", "소송", "최대주주", "주식소각", "자산양수도", "영업양수도")


def clean_title(t: str, outlet: str | None = None) -> str:
    """검색 결과 제목 끝의 매체 표기(' - 조선비즈', ' | 한경매거진&북', ':: 공감언론 뉴시스 ::')를 뗀다."""
    t = re.sub(r"\s*::[^:]*::\s*$", "", t or "").strip()
    for _ in range(2):
        m = re.search(r"\s+[-|│ㅣ]\s+([^-|│ㅣ]{1,20})$", t)
        tail = m.group(1) if m else ""
        if m and ((outlet and outlet in tail) or re.search(
                r"일보|신문|뉴스|경제|비즈|매거진|방송|TV|타임스|헤럴드|미디어|저널|데일리|투데이|포스트|&북|\.(?:com|kr|net)", tail)):
            t = t[:m.start()].strip()
    return t


def _news_disc(c: dict) -> None:
    inp, data = c["inp"], c["data"]
    data.news = []
    for i, n in enumerate(inp.news, 1):
        outlet = outlet_name(n.url)
        data.news.append({"no": i, "id": n.id, "outlet": outlet,
                          "date": n.published_at.date().isoformat() if n.published_at else None,
                          "title": clean_title(n.title, outlet), "url": n.url,
                          "gist": " ".join(n.analysis.summary[:2]) if n.analysis else ""})
    bb = {b.rcept_no: b for b in inp.buybacks}
    pre = inp.prelim
    out = []
    for d in inp.disclosures:
        if not any(k in d.report_nm for k in KEY_DISCLOSURE):
            continue
        gist = None
        if pre and d.rcept_no == pre.rcept_no:
            rv, op = pre.rows.get("revenue"), pre.rows.get("operating_income")
            parts = [f"매출 {fmt.money(f(rv.cur))}" if rv else None, f"영업이익 {fmt.money(f(op.cur))}" if op else None]
            gist = f"{pre.key} " + ", ".join(p for p in parts if p)
        elif d.rcept_no in bb:
            b = bb[d.rcept_no]
            parts = [f"보통주 {b.shares_common:,}주" if b.shares_common else None,
                     f"약 {fmt.money(b.amount_common)}" if b.amount_common else None,
                     f"{fmt.md(b.start)}~{fmt.md(b.end)}" if b.start and b.end else None,
                     f"목적: {b.purpose}" if b.purpose else None]
            gist = ", ".join(p for p in parts if p)
        out.append({"date": d.rcept_dt.isoformat(), "title": re.sub(r"\s+", " ", d.report_nm), "url": d.url,
                    "rcept_no": d.rcept_no, "gist": gist})
    data.disclosures = out[:6]
