"""리포트 v2: 분석(합성 자료), 조사·방향어 다듬기, 숫자 추적, 문체 검사, 컨센서스 검증, 렌더링."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from app.agents.company import writer
from app.agents.company.analysis import Consensus, Inputs, analyze, clean_title, ret_between
from app.agents.company.render import Text, render_report, table_html, ticks
from app.core.dart.history import QRow, qkey
from app.core.dart.prelim import Prelim, PrelimFiling, PrelimRow
from app.core.dart.shares import BuybackDecision, Dividend, Shares
from app.core.peers import PeerRow
from app.domain.binding import render, untraceable_numbers
from app.domain.models import (Block, CorpProfile, Headline, Metric, NewsItem, PeriodicFiling, ReportDraft,
                               VerificationIssue, VerificationResult)

OPM = [10, 12, 15, 18, 20, 25, 22, 15, 10, 6, 3, 2, 4, 6, 9, 12, 15, 18, 22, 26, 30, 35]   # 1Q21~2Q26


def _quarters():
    out, y, q = [], 2021, 1
    for i, m in enumerate(OPM):
        rev = D(1000e8) + D(i) * D(20e8)
        op = rev * D(m) / 100
        filed = {1: (y, 5, 15), 2: (y, 8, 14), 3: (y, 11, 14), 4: (y + 1, 3, 15)}[q]
        out.append(QRow(key=qkey(y, q), year=y, q=q, revenue=rev, operating_income=op, net_income=op * D("0.8"),
                        net_income_owners=op * D("0.8"), equity_owners=D(10_000e8) + D(i) * D(100e8),
                        rcept_no=f"{filed[0]}{filed[1]:02d}{filed[2]:02d}000001", fs_div="CFS"))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def _px(start=date(2016, 1, 4), end=date(2026, 10, 8), base=50_000.0, drift=0.0003):
    out, d, i = [], start, 0
    while d <= end:
        if d.weekday() < 5:
            out.append((d, base * (1 + drift * i)))
            i += 1
        d += timedelta(days=1)
    return out


def _cum(revenue, cfo, capex, cash, debt, ar, inv):
    return {"revenue": D(revenue), "net_income": D(cfo) * D("0.8"), "cfo": D(cfo), "capex": D(-capex),
            "intangible_capex": D(-1e9), "buyback": D(-5e9), "dividends_paid": D(-3e9), "cash": D(cash), "st_fin": None,
            "st_amort": None, "st_fvpl": None, "st_borrow": D(debt), "cur_ltd": None, "bonds": None, "lt_borrow": None,
            "receivables": D(ar), "inventory": D(inv), "equity_owners": None, "rcept_no": "x"}


def _inputs(*, prelim: Prelim | None = None, consensus: Consensus | None = None) -> Inputs:
    qs = _quarters()
    n = lambda y: 100_000_000 if y < 2025 else 98_000_000          # 2025년에 2% 소각
    shares = {(r.year, r.q): Shares(r.year, "x", n(r.year), 0, 0, 0, n(r.year), 0) for r in qs}
    seg_cum = {}
    for (y, q) in [(2024, 2), (2024, 3), (2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]:
        k = q   # 누적이라 분기 수에 비례
        seg_cum[(y, q)] = {"A 부문": {"rev": D(600e8) * k, "op": D(200e8) * k},
                           "B 부문": {"rev": D(400e8) * k, "op": D(-10e8) * k if (y, q) == (2026, 2) else D(20e8) * k},
                           "C": {"rev": D(50e8) * k, "op": D(5e8) * k}}
    # 2Q26만 B 부문 적자: 누적 값이 1Q26보다 작아지도록 조정
    seg_cum[(2026, 2)]["B 부문"]["op"] = seg_cum[(2026, 1)]["B 부문"]["op"] - D(15e8)
    firsts = [PrelimFiling(f"{d.strftime('%Y%m%d')}000001", d, "영업(잠정)실적(공정공시)", False)
              for d in (date(2023, 4, 7), date(2023, 7, 7), date(2023, 10, 10), date(2024, 1, 9), date(2024, 4, 5),
                        date(2024, 7, 5), date(2024, 10, 8), date(2025, 1, 8), date(2025, 4, 8), date(2025, 7, 8))]
    px = _px()
    ix = _px(base=2500.0, drift=0.0001)
    peers = [PeerRow("00000001", "000001", "테스트(주)", True, 5e12, 3e11, 2e12, 1.9e12, 50_000.0, 100_000_000,
                     roe=15.0, pbr=2.5, per=16.0, induty_code="264"),
             PeerRow("2", "000002", "가나전자", False, 9e12, 9e11, 3e12, 2.8e12, 1.0, 1, roe=30.0, pbr=3.0, per=10.0,
                     induty_code="2612"),
             PeerRow("3", "000003", "다라전자", False, 1e12, 5e10, 1e12, 1e12, 1.0, 1, roe=5.0, pbr=1.0, per=20.0,
                     induty_code="2621")]
    return Inputs(
        corp=CorpProfile(corp_code="00000001", corp_name="테스트(주)", stock_code="000001", corp_cls="Y",
                         induty_code="264", acc_mt=12),
        today=date(2026, 10, 11),
        filing=PeriodicFiling(rcept_no="20260814000001", report_nm="반기보고서 (2026.06)", rcept_dt=date(2026, 8, 14),
                              bsns_year=2026, reprt_code="11012"),
        quarters=qs, prelim=prelim,
        cum={(2023, 4): _cum(5e12, 6e11, 3e11, 1e12, 2e11, 4e11, 5e11),
             (2024, 4): _cum(5.5e12, 7e11, 3e11, 1.2e12, 2e11, 4.5e11, 5e11),
             (2025, 4): _cum(6e12, 8e11, 3e11, 1.4e12, 2e11, 5e11, 5.2e11),
             (2025, 2): _cum(3e12, 3e11, 1.4e11, 1.1e12, 2e11, 4.6e11, 5e11),
             (2026, 2): _cum(3.4e12, 6e11, 1.6e11, 1.9e12, 2e11, 6e11, 5.5e11)},
        shares=shares, splits=[], dividends={y: Dividend(y, 2000, 1000, 25.0) for y in (2023, 2024, 2025)},
        buybacks=[BuybackDecision("20260901000001", "2026-09-01", 1_000_000, 50_000_000_000, "2026-09-02",
                                  "2026-11-30", "주식소각", "장내매수")],
        px=px, index_px=ix, index_name="KOSPI", pref_px=None, prelim_firsts=firsts, segments=seg_cum, peers=peers,
        consensus=consensus, news=[], disclosures=[])


def test_analyze_core_facts_and_charts():
    data = analyze(_inputs())
    f = {k: m.text for k, m in data.facts.items()}
    assert data.notes == []
    assert f["opm_rank.hist"] == "22개 분기 중 최고"
    assert f["opm_prev_peak_q.hist"] == "2Q22" and f["opm_prev_peak.hist"] == "25.0%"
    assert f["opm_trough_q.hist"] == "4Q23"
    assert set(data.charts) >= {"hero", "op", "segments", "band", "peers", "cash", "events", "rel"}
    assert data.charts["hero"]["title"] == "영업이익률이 2021년 이후 가장 높다"
    assert data.table_unit == "억원"                      # 매출 1조 미만 → 억원 표
    assert f["bb_purpose.now"] == "주식소각"
    assert f["shares_cut_common.since"] == "2.0% 감소"


def test_segments_standalone_from_cumulative():
    data = analyze(_inputs())
    f = {k: m for k, m in data.facts.items()}
    # 2Q26 단독 = 반기 누적 − 1분기 누적: A 부문 400억 − 200억 = 200억
    assert f["seg1_op.2Q26"].text == "200억원"
    assert f["seg2_loss.2Q26"].text == "15억원"
    assert f["seg2_first_loss.2Q26"].text.startswith("최근 8개 분기 중 처음")
    assert data.charts["segments"]["title"] == "A 이익이 커지는 동안 B는 적자로" or "B" in data.charts["segments"]["title"]


def test_events_excess_return_and_rel():
    data = analyze(_inputs())
    ev = data.tables["events"]
    assert len(ev) == 10
    e = ev[0]
    assert e["ex0"] == pytest.approx(e["day_stock"] - e["day_index"], abs=1e-9)
    assert data.charts["rel"]["rows"][0][1] == pytest.approx(100.0)


def test_prelim_path_uses_disclosed_growth_and_consensus():
    p = Prelim("20261008800004", date(2026, 10, 8), "연결재무제표기준영업(잠정)실적(공정공시)", 2026, 3, "억원", True,
               rows={"revenue": PrelimRow(cur=D(1500e8), qoq=3.1, yoy=20.0),
                     "operating_income": PrelimRow(cur=D(600e8), qoq=5.55, yoy=782.50, cum=D(1500e8), cum_yoy=12.0)})
    cons = Consensus("3Q26", 1600e8, 580e8, "에프앤가이드", "n1", "서울경제", "q")
    data = analyze(_inputs(prelim=p, consensus=cons))
    f = {k: m.text for k, m in data.facts.items()}
    assert data.latest["label"] == "3Q26 잠정"
    assert f["op_yoy.3Q26P"] == "+782.5%"                 # 다시 계산하지 않고 공시 값
    assert f["op_vs_cons.3Q26P"] == "3.4% 상회"
    assert f["rev_vs_cons.3Q26P"] == "6.2% 하회"
    assert data.tables["latest"]["heads"][:3] == ["3Q26P", "컨센서스", "차이"]
    assert data.charts["op"]["cons"] == pytest.approx(580e8)


def test_render_without_draft_has_no_raw_slots():
    data = analyze(_inputs())
    html = render_report(data, None)
    assert html.startswith("<style>") and '<div class="fs-report">' in html
    assert "{{" not in html and "실적</h2>" in html
    assert html.count('<div class="cv d">') == html.count('<div class="cv m">') >= 8


def test_render_with_draft_slots_citations_and_heads():
    data = analyze(_inputs())
    data.news = [{"no": 1, "id": "n1", "outlet": "서울경제", "date": "2026-10-08", "title": "기사", "url": "https://a.kr/1"}]
    B = lambda i, h, t: Block(id=i, head=h, text=t)
    d = ReportDraft(title=Headline(text="제목"), summary=[B("summary.1", "요약", "영업이익률은 {{opm.2Q26}}으로 최고임[n1].")],
                    earnings_head=Headline(text="실적: 이익 증가"), earnings_a=[], earnings_b=[],
                    valuation_head=Headline(text=""), valuation_a=[], valuation_b=[], cash_head=Headline(text=""),
                    cash_a=[], cash_b=[], market_head=Headline(text=""), market_a=[], market_b=[], market_c=[])
    html = render_report(data, d)
    assert "35.0%</span>로" in html                       # 조사 보정
    assert '<sup class="fn"' in html and ">1</sup>" in html
    assert "<h2>실적: 이익 증가</h2>" in html                # 절 이름이 두 번 붙지 않는다


def _m(i, t):
    return Metric(id=i, label=i, period="x", value=None, unit="text", formula=i, text=t)


@pytest.mark.parametrize("src,want", [
    ("{{a.x}}으로", "55.1%로"), ("{{b.x}}을", "3.04배를"), ("{{d.x}}으로", "51일로"), ("{{e.x}} 하락함", "35% 하락함"),
    ("{{g.x}} 증가함", "782.5% 증가함"), ("{{h.x}} 늘어", "45.3조원 늘어"), ("{{j.x}}였으며", "105.5조원이었으며"),
    ("{{k.x}}가 적자", "8,188억원이 적자"), ("QoQ {{g.x}},", "QoQ +782.5%,"), ("{{m.x}}함", "1.8% 상회함"),
    ("{{h.x}} 변화함", "45.3조원 증가함")])
def test_slot_tidy(src, want):
    f = {k + ".x": _m(k, v) for k, v in dict(a="55.1%", b="3.04배", d="51일", e="35% 하락", g="+782.5%",
                                             h="45.3조원 증가", j="105.5조원", k="8,188억원", m="1.8% 상회").items()}
    assert render(src, f) == want


def test_untraceable_numbers_allows_periods_and_report_tokens():
    assert untraceable_numbers("3Q26 잠정 {{op.3Q26P}}, 1~3분기 누적, 4Q25·1Q26[n1]", []) == []
    assert untraceable_numbers("43개 분기 중 최고", [], {"43"}) == []
    assert untraceable_numbers("영업이익 107.4조원", [], {"21.3"}) == ["107.4"]


def test_parse_krw():
    assert writer.parse_krw("106조29억원") == pytest.approx(106.0029e12)
    assert writer.parse_krw("1조2,000억 원") == pytest.approx(1.2e12)
    assert writer.parse_krw("3,500억원") == pytest.approx(3.5e11)
    assert writer.parse_krw("약 100조") is None


def _draft(**kw):
    B = lambda i, h="h", t="t": Block(id=i, head=h, text=t)
    base = dict(title=Headline(text="제목"), summary=[B(f"summary.{i}") for i in range(1, 5)],
                earnings_head=Headline(text="e"), earnings_a=[B("earnings_a.1")], earnings_b=[],
                valuation_head=Headline(text="v"), valuation_a=[], valuation_b=[], cash_head=Headline(text="c"),
                cash_a=[], cash_b=[], market_head=Headline(text="m"), market_a=[], market_b=[], market_c=[])
    base.update(kw)
    return ReportDraft(**base)


def test_check_draft_counts_and_ids():
    assert writer._check_draft(_draft()) is None
    assert "summary" in writer._check_draft(_draft(summary=[]))
    bad = _draft(earnings_a=[Block(id="earnings_a.2", head="h", text="t")])
    assert "earnings_a.1" in writer._check_draft(bad)


def test_style_issues_flags_chart_title_echo_and_wrapups():
    data = analyze(_inputs())
    title = data.charts["events"]["title"]
    d = _draft(market_b=[Block(id="market_b.1", head=title.replace("은", ""), text="평균은 1.3%p임.")],
               cash_a=[Block(id="cash_a.1", head="현금 증가", text="현금이 늘어나는 구조임. 주목할 필요가 있음.")])
    got = {i.claim_id: i.detail for i in writer.style_issues(d, data)}
    assert "market_b.1" in got and "cash_a.1" in got
    assert "주목할 필요" in got["cash_a.1"]


def test_finalize_drops_failed_blocks_but_keeps_style_only():
    d = _draft(earnings_a=[Block(id="earnings_a.1", head="h", text="t")],
               cash_a=[Block(id="cash_a.1", head="h", text="t")])
    ver = VerificationResult(passed=False, issues=[
        VerificationIssue(claim_id="earnings_a.1", type="untraceable_number", detail="x"),
        VerificationIssue(claim_id="cash_a.1", type="style", detail="y"),
        VerificationIssue(claim_id="title", type="partial", detail="z")])
    out, dropped = writer.finalize(d, ver, "대체 제목")
    assert out.earnings_a == [] and len(out.cash_a) == 1
    assert out.title.text == "대체 제목" and dropped == ["earnings_a.1", "title"]


async def test_extract_consensus_validates_quote_period_and_scale(monkeypatch):
    body = "삼성전자 3분기 영업이익 컨센서스(증권사 추정치 평균)는 현재 105조5000억원 수준으로 낮아졌다. 에프앤가이드 집계."
    news = [NewsItem(id="n1", title="t", url="https://biz.chosun.com/a", source="biz.chosun.com", published_at=None)]
    replies = []

    async def fake(schema, messages, **kw):
        return replies.pop(0)
    monkeypatch.setattr(writer.llm, "structured", fake)
    ok = writer._Cons(found=True, news_id="n1", period="3Q26 (3분기)", provider="에프앤가이드 컨센서스",
                      revenue_text=None, op_text="105조5000억원",
                      quote="삼성전자 3분기 영업이익 컨센서스(증권사 추정치 평균)는 현재 105조5000억원 수준으로 낮아졌다.")
    replies.append(ok)
    c = await writer.extract_consensus("3Q26", "3분기", 107.4e12, news, {"n1": body})
    assert c and c.operating_income == pytest.approx(105.5e12) and c.provider == "에프앤가이드" and c.outlet == "조선비즈"
    replies.append(ok.model_copy(update={"quote": "본문에 없는 문장"}))
    assert await writer.extract_consensus("3Q26", "3분기", 107.4e12, news, {"n1": body}) is None
    replies.append(ok.model_copy(update={"period": "2Q26"}))
    assert await writer.extract_consensus("3Q26", "3분기", 107.4e12, news, {"n1": body}) is None
    replies.append(ok)
    assert await writer.extract_consensus("3Q26", "3분기", 20e12, news, {"n1": body}) is None   # 실제의 5배 → 버림


def test_text_citations_and_unknown_slot():
    tx = Text({"op.x": _m("op", "107.4조원")}, [{"no": 2, "id": "n4", "outlet": "조선비즈", "date": "2026-10-07",
                                               "title": "기사"}])
    out = tx("영업이익 {{op.x}}[n4][n9] 그리고 {{zz.x}}")
    assert ">2</sup>" in out and "n9" not in out and "–" in out


def test_ticks_and_table_cells():
    assert ticks(0, 107.4, 4) == [0, 30, 60, 90, 120]
    assert ticks(-5.2, 1.9, 4) == [-6, -4, -2, 0, 2]
    html = table_html({"title": "t", "unit": "u", "heads": ["a"], "focal": 0, "rows": [
        {"head": "r", "cells": [{"v": "-2.6", "sign": -2.6}]}]})
    assert 'class="dn"' in html and 'class="f"' in html


def test_ret_between_and_clean_title():
    px = [(date(2025, 1, 1) + timedelta(days=i), 100.0 + i) for i in range(400)]
    assert ret_between(px, 365) == pytest.approx((499 / 134 - 1) * 100)
    assert clean_title("삼성전자, 실적 발표 - 조선비즈", "조선비즈") == "삼성전자, 실적 발표"
    assert clean_title("A - B 합병 추진", "x") == "A - B 합병 추진"
