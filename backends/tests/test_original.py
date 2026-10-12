"""정기보고서 원문 재무제표 읽기(재무 API가 013을 주는 분기)와 누적값으로 분기 값 만들기."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.dart import history, original
from app.core.dart.financials import parse_lines
from app.core.dart.viewer import decode
from app.domain.accounts import find_line
from app.domain.models import FinancialSnapshot

B = Decimal(10) ** 8   # 억원


def _doc(statements: str, unit: str = "백만원") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?><DOCUMENT><BODY>
<TITLE ATOC="Y">1. 요약재무정보</TITLE><P>요약</P>
<TITLE ATOC="Y">2. 연결재무제표</TITLE>
<TABLE BORDER="0"><TR><TD>주식회사 가나와 그 종속기업</TD><TD>(단위: {unit})</TD></TR></TABLE>
{statements}
<TITLE ATOC="Y">3. 연결재무제표 주석</TITLE>
<TABLE BORDER="1"><TR><TH>과 목</TH><TH>제16기 반기</TH></TR><TR><TD>영업이익</TD><TD>999</TD></TR></TABLE>
</BODY></DOCUMENT>"""


BS = """<TABLE BORDER="1"><THEAD><TR><TH>과 목</TH><TH>제16기 반기말</TH><TH>제15기말</TH></TR></THEAD>
<TR><TD>자 산</TD><TD></TD><TD></TD></TR>
<TR><TD>자산총계</TD><TD>700,000</TD><TD>690,000</TD></TR>
<TR><TD>Ⅰ. 지배기업 주주지분</TD><TD>55,670</TD><TD>52,864</TD></TR>
<TR><TD>자본총계</TD><TD>57,704</TD><TD>54,144</TD></TR></TABLE>"""

# 반기: 당기 열이 누적 하나뿐인 형식
IS_CUM = """<TABLE BORDER="1"><THEAD><TR><TH>과 목</TH><TH>제16기 반기</TH><TH>제15기 반기</TH><TH>제15기</TH></TR></THEAD>
<TR><TD>X.영업이익</TD><TD>4,178</TD><TD>3,373</TD><TD>5,246</TD></TR>
<TR><TD>XIV.당기순이익</TD><TD>3,003</TD><TD>2,686</TD><TD>3,900</TD></TR>
<TR><TD>지배기업주주지분순이익</TD><TD>2,996</TD><TD>2,670</TD><TD>4,121</TD></TR>
<TR><TD>기본주당이익(원)</TD><TD>7,530원</TD><TD>6,723원</TD><TD>10,254원</TD></TR></TABLE>"""

# 1분기: 한 기간이 '세부·합계' 두 칸인 옛 형식
IS_SPLIT = """<TABLE BORDER="1"><THEAD><TR><TH>과 목</TH><TH COLSPAN="2">제15기 1분기</TH><TH COLSPAN="2">제14기 1분기</TH></TR></THEAD>
<TR><TD>Ⅰ. 순이자이익</TD><TD></TD><TD>3,061</TD><TD></TD><TD>2,642</TD></TR>
<TR><TD>1. 이자수익</TD><TD>4,309</TD><TD></TD><TD>3,624</TD><TD></TD></TR>
<TR><TD>Ⅸ. 영업이익</TD><TD></TD><TD>1,901</TD><TD></TD><TD>1,700</TD></TR>
<TR><TD>XIII. 당기순이익</TD><TD></TD><TD>1,464</TD><TD></TD><TD>1,300</TD></TR>
<TR><TD>지배기업주주지분순이익</TD><TD>1,453</TD><TD></TD><TD>1,290</TD><TD></TD></TR></TABLE>"""

# 3분기: 3개월·누적 칸
IS_3M = """<TABLE BORDER="1"><THEAD><TR><TH ROWSPAN="2">과 목</TH><TH COLSPAN="2">제15기 3분기</TH><TH COLSPAN="2">제14기 3분기</TH></TR>
<TR><TH>3개월</TH><TH>누적</TH><TH>3개월</TH><TH>누적</TH></TR></THEAD>
<TR><TD>영업이익(손실)</TD><TD>1,000</TD><TD>(3,000)</TD><TD>900</TD><TD>2,500</TD></TR>
<TR><TD>분기순이익</TD><TD>800</TD><TD>2,400</TD><TD>700</TD><TD>2,000</TD></TR></TABLE>"""

CF = """<TABLE BORDER="1"><TR><TH>과목</TH><TH>제16기 반기</TH><TH>제15기 반기</TH></TR>
<TR><TD>Ⅰ. 영업활동으로부터의 현금흐름</TD><TD>2,958</TD><TD>(3,569)</TD></TR>
<TR><TD>6. 보통주 배당 지급</TD><TD>(760)</TD><TD>(1,048)</TD></TR></TABLE>"""

SCE = """<TABLE BORDER="1"><TR><TH>과목</TH><TH COLSPAN="2">지배기업 주주지분</TH><TH>총계</TH></TR>
<TR><TD></TD><TD>자본금</TD><TD>이익잉여금</TD><TD></TD></TR>
<TR><TD>당기순이익</TD><TD>-</TD><TD>2,996</TD><TD>3,003</TD></TR></TABLE>"""


def _snap(xml: str, code: str) -> FinancialSnapshot:
    rows, fs = original.parse_statements(xml, code, "20231005000482")
    return FinancialSnapshot(corp_code="x", rcept_no="20231005000482", bsns_year=2023, reprt_code=code,
                             fs_div=fs, period_label="", lines=parse_lines(rows))


def test_half_year_cumulative_only_statements():
    s = _snap(_doc(BS + IS_CUM + SCE + CF), "11012")
    op, ni = find_line(s, "operating_income").line, find_line(s, "net_income_owners").line
    assert op.thstrm_amount is None and op.thstrm_add_amount == 4178 * 10 ** 6       # 반기 열은 누적
    assert ni.thstrm_add_amount == 2996 * 10 ** 6 and ni.sj_div == "IS"             # 자본변동표 줄은 쓰지 않는다
    assert find_line(s, "equity_owners").line.thstrm_amount == 55670 * 10 ** 6
    assert find_line(s, "cfo").line.thstrm_add_amount == 2958 * 10 ** 6
    assert find_line(s, "dividends_paid").line.thstrm_add_amount == -760 * 10 ** 6
    assert all(ln.account_nm != "기본주당이익(원)" for ln in s.lines)               # '7,530원'은 금액이 아니다
    assert s.fs_div == "CFS" and not any(ln.thstrm_add_amount == 999 * 10 ** 6 for ln in s.lines)   # 주석 표는 안 읽는다


def test_old_split_columns_take_filled_cell():
    s = _snap(_doc(IS_SPLIT, unit="천원"), "11013")
    assert find_line(s, "operating_income").line.thstrm_amount == 1901 * 1000
    owners = find_line(s, "net_income_owners").line
    assert owners.thstrm_amount == owners.thstrm_add_amount == 1453 * 1000           # 1분기는 3개월 = 누적


def test_three_month_and_cumulative_columns():
    s = _snap(_doc(IS_3M), "11014")
    op = find_line(s, "operating_income").line
    assert (op.thstrm_amount, op.thstrm_add_amount) == (1000 * 10 ** 6, -3000 * 10 ** 6)


def test_separate_statements_fallback_and_euc_kr():
    xml = _doc(IS_CUM).replace("2. 연결재무제표", "2. 재무제표")
    rows, fs = original.parse_statements(xml, "11012", "r")
    assert fs == "OFS" and rows
    assert original.parse_statements("<DOCUMENT>재무제표 없음</DOCUMENT>", "11012", "r") == ([], "CFS")
    assert "연결재무제표" in decode(_doc(IS_CUM).encode("cp949"))


@pytest.mark.asyncio
async def test_history_fills_from_original_and_derives_quarter(monkeypatch):
    def snap(y, q, cum_op, cum_ni):
        code = history.RC[q]
        add = lambda v: {"thstrm_add_amount": str(v)} if q in (2, 3) else {"thstrm_amount": str(v), "thstrm_add_amount": str(v)}
        rows = [{"sj_div": "IS", "account_id": "", "account_nm": "영업이익", "ord": "1", **add(cum_op)},
                {"sj_div": "IS", "account_id": "", "account_nm": "당기순이익", "ord": "2", **add(cum_ni)},
                {"sj_div": "BS", "account_id": "", "account_nm": "자본총계", "ord": "3", "thstrm_amount": "1"}]
        return FinancialSnapshot(corp_code="x", rcept_no=f"r{y}{q}", bsns_year=y, reprt_code=code, fs_div="CFS",
                                 period_label="", lines=parse_lines(rows))

    api = {(2023, 3): snap(2023, 3, 600 * B, 300 * B)}
    orig = {(2023, 1): snap(2023, 1, 200 * B, 100 * B), (2023, 2): snap(2023, 2, 450 * B, 220 * B)}

    async def fake_snap(dart, corp, y, q, acc):
        return api.get((y, q))

    async def fake_original(dart, corp, periods, acc):
        assert set(periods) == {(2022, 4), (2023, 1), (2023, 2)}          # API에 없는 최근 분기만
        return {p: orig[p] for p in periods if p in orig}

    monkeypatch.setattr(history, "_snap", fake_snap)
    monkeypatch.setattr(history, "from_original", fake_original)
    monkeypatch.setattr(history, "ORIGINAL_QUARTERS", 4)
    h = history.quarterly_history(None, "x", (2023, 3), first_year=2022)
    rows = {r.key: r for r in (await h).quarters}
    assert rows["1Q23"].operating_income == 200 * B
    assert rows["2Q23"].operating_income == 250 * B and rows["2Q23"].net_income == 120 * B   # 반기 누적 - 1분기 누적
    assert rows["3Q23"].operating_income == 150 * B   # API 3분기 스냅숏에 누적만 있으면 반기 누적(원문)을 뺀다
