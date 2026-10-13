from decimal import Decimal as D

from app.domain.accounts import find_line, industry_group, normalize_name
from app.domain.binding import display, missing_slots, render, untraceable_numbers
from app.domain.models import FinancialLine, FinancialSnapshot, Metric


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


def test_financial_group_detection():
    s = snap([line("BS", "ifrs-full_Assets", "자산총계", 1, D(1))])
    assert industry_group(s, "64992") == "financial"
    assert industry_group(snap(BASE), "64992") == "general"  # 유동자산 줄이 있으면 일반
    assert industry_group(s, "26410") == "general"


def test_display_and_render():
    opm = Metric(id="op_margin", label="영업이익률", period="2025FY", value=D(10), unit="%", formula="op/rev")
    rev = Metric(id="revenue", label="매출액", period="2025FY", value=D(1_234_567_890_000), unit="KRW", formula="rev")
    assert display(opm) == "10.0%"
    assert display(rev) == "12,346억 원"
    by_slot = {x.slot_id: x for x in (opm, rev)}
    assert render("영업이익률은 {{op_margin.2025FY}}다", by_slot) == "영업이익률은 10.0%다"
    assert missing_slots("{{foo.2025FY}}", by_slot) == ["foo.2025FY"]


def test_untraceable_numbers():
    src = ["회사는 3,000억 원 규모 수주를 발표했다"]
    t = "2025년 반기 {{op_margin.2025H1}}로 개선됐고 3,000억 원 수주[n1]가 있다. 법 제21조의2와 별표 1 기준. 매출은 15% 늘었다."
    assert untraceable_numbers(t, src) == ["15"]
