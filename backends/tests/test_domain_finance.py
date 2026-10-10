from decimal import Decimal as D

from app.domain.accounts import find_line, normalize_name
from app.domain.binding import display, facts_table, missing_slots, render, untraceable_numbers
from app.domain.metrics import compute_metrics, industry_group, signed_growth
from app.domain.models import FinancialLine, FinancialSnapshot


def line(sj, aid, nm, ord_, cur=None, prev=None, cur_add=None, prev_add=None):
    return FinancialLine(sj_div=sj, account_id=aid, account_nm=nm, ord=ord_, thstrm_amount=cur,
                         frmtrm_amount=prev, thstrm_add_amount=cur_add, frmtrm_add_amount=prev_add)


def snap(lines, reprt="11011", fs="CFS"):
    return FinancialSnapshot(corp_code="x", rcept_no="r1", bsns_year=2025, reprt_code=reprt, fs_div=fs,
                             period_label="", lines=lines)


BASE = [
    line("IS", "ifrs-full_Revenue", "매출액", 1, D(1000), D(800)),
    line("IS", "ifrs-full_GrossProfit", "매출총이익", 3, D(300), D(200)),
    line("IS", "dart_OperatingIncomeLoss", "영업이익(손실)", 5, D(100), D(-50)),
    line("IS", "ifrs-full_ProfitLoss", "당기순이익", 9, D(-20), D(40)),
    line("IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 10, D(80), D(30)),
    line("BS", "ifrs-full_Assets", "자산총계", 1, D(5000), D(4000)),
    line("BS", "ifrs-full_CurrentAssets", "유동자산", 2, D(2000), D(1500)),
    line("BS", "ifrs-full_Liabilities", "부채총계", 3, D(3000), D(2500)),
    line("BS", "ifrs-full_CurrentLiabilities", "유동부채", 4, D(1000), D(900)),
    line("BS", "ifrs-full_Equity", "자본총계", 5, D(2000), D(1500)),
    line("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분", 6, D(1600), D(1200)),
]


def test_normalize_name():
    assert normalize_name("Ⅰ. 매출액 (주3)") == "매출액"
    assert normalize_name("1. 영업 이익(주석22)") == "영업이익"


def test_find_by_name_when_no_id():
    s = snap([line("IS", "-표준계정코드 미사용-", "영업수익", 1, D(10))])
    f = find_line(s, "revenue")
    assert f and f.method == "name"


def test_metrics_annual_sign_rules_and_roe():
    m = compute_metrics(snap(BASE), "general")
    assert m["revenue_growth"].value == D(25)
    assert m["op_income_growth"].value is None and m["op_income_growth"].note == "흑자 전환"
    assert m["net_income_growth"].note == "적자 전환"
    assert m["gross_margin"].value == D(30)
    assert m["debt_ratio"].value == D(150)
    assert m["roe"].value == D(5)  # 80 / 1600
    assert m["revenue"].inputs[0].column == "thstrm_amount"


def test_quarter_uses_cumulative_and_annualizes():
    lines = [line("IS", "ifrs-full_Revenue", "매출액", 1, D(300), D(250), D(600), D(500)),
             line("IS", "ifrs-full_ProfitLoss", "반기순이익", 2, D(30), D(20), D(60), D(50)),
             line("BS", "ifrs-full_Equity", "자본총계", 1, D(1200), D(1000))]
    m = compute_metrics(snap(lines, reprt="11012", fs="OFS"), "general")
    assert m["revenue_growth"].value == D(20)          # 600 / 500 - 1
    assert m["roe"].note == "연환산" and m["roe"].value == D(10)  # 60*2/1200
    assert m["revenue"].period == "2025H1"


def test_capital_impairment_and_none_propagation():
    lines = [line("BS", "ifrs-full_Liabilities", "부채총계", 1, D(100)),
             line("BS", "ifrs-full_Equity", "자본총계", 2, D(-5))]
    m = compute_metrics(snap(lines), "general")
    assert m["debt_ratio"].value is None and m["debt_ratio"].note == "자본잠식"
    assert m["current_ratio"].value is None and m["current_ratio"].note is None
    assert m["revenue_growth"].value is None


def test_signed_growth_both_negative():
    assert signed_growth(D(-1), D(-2)) == (None, "적자 지속")


def test_financial_group_detection():
    s = snap([line("BS", "ifrs-full_Assets", "자산총계", 1, D(1))])
    assert industry_group(s, "64992") == "financial"
    assert industry_group(snap(BASE), "64992") == "general"  # 유동자산 줄이 있으면 일반
    m = compute_metrics(s, "financial")
    assert "current_ratio" not in m and "equity_ratio" in m


def test_display_and_render():
    m = compute_metrics(snap(BASE), "general")
    assert display(m["op_margin"]) == "10.0%"
    big = compute_metrics(snap([line("IS", "ifrs-full_Revenue", "매출액", 1, D(1_234_567_890_000))]), "general")
    assert display(big["revenue"]) == "12,346억 원"
    by_slot = {x.slot_id: x for x in m.values()}
    assert render("영업이익률은 {{op_margin.2025FY}}다", by_slot) == "영업이익률은 10.0%다"
    assert missing_slots("{{foo.2025FY}}", by_slot) == ["foo.2025FY"]
    assert any(r["id"] == "op_income_growth.2025FY" and r["display"] == "흑자 전환" for r in facts_table(m))


def test_untraceable_numbers():
    src = ["회사는 3,000억 원 규모 수주를 발표했다"]
    t = "2025년 반기 {{op_margin.2025H1}}로 개선됐고 3,000억 원 수주[n1]가 있다. 법 제21조의2와 별표 1 기준. 매출은 15% 늘었다."
    assert untraceable_numbers(t, src) == ["15"]
