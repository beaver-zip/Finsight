"""계정 매핑: 표준 지표마다 account_id 후보 → 계정명 동의어 순으로 줄을 찾는다. 못 찾으면 None(0으로 채우지 않는다)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.domain.ksic import is_financial_division
from app.domain.models import FinancialLine, FinancialSnapshot, IndustryGroup

Method = Literal["id", "name"]


@dataclass(frozen=True)
class AccountSpec:
    tables: tuple[str, ...]          # 찾는 순서. 손익은 IS 먼저, 없으면 CIS
    ids: tuple[str, ...]
    names: tuple[str, ...]


PL = ("IS", "CIS")
STANDARD: dict[str, AccountSpec] = {
    "revenue": AccountSpec(PL, ("ifrs-full_Revenue",), ("매출액", "수익(매출액)", "영업수익", "매출")),
    "cost_of_sales": AccountSpec(PL, ("ifrs-full_CostOfSales",), ("매출원가",)),
    "gross_profit": AccountSpec(PL, ("ifrs-full_GrossProfit",), ("매출총이익", "매출총이익(손실)")),
    "operating_income": AccountSpec(PL, ("dart_OperatingIncomeLoss",), ("영업이익", "영업이익(손실)")),
    "net_income": AccountSpec(PL, ("ifrs-full_ProfitLoss",),
                              ("당기순이익", "반기순이익", "분기순이익", "당기순이익(손실)", "반기순이익(손실)",
                               "분기순이익(손실)")),
    "net_income_owners": AccountSpec(PL, ("ifrs-full_ProfitLossAttributableToOwnersOfParent",),
                                     ("지배기업소유주지분", "지배기업의소유주지분", "지배기업주주지분순이익",
                                      "지배기업소유주지분순이익")),
    "total_assets": AccountSpec(("BS",), ("ifrs-full_Assets",), ("자산총계",)),
    "current_assets": AccountSpec(("BS",), ("ifrs-full_CurrentAssets",), ("유동자산",)),
    "total_liabilities": AccountSpec(("BS",), ("ifrs-full_Liabilities",), ("부채총계",)),
    "current_liabilities": AccountSpec(("BS",), ("ifrs-full_CurrentLiabilities",), ("유동부채",)),
    "total_equity": AccountSpec(("BS",), ("ifrs-full_Equity",), ("자본총계",)),
    "equity_owners": AccountSpec(("BS",), ("ifrs-full_EquityAttributableToOwnersOfParent",),
                                 ("지배기업소유주지분", "지배기업의소유주지분", "지배기업주주지분")),
    "cfo": AccountSpec(("CF",), ("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
                       ("영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로부터의현금흐름")),
    # 현금흐름의 질·순현금·운전자본·주주환원(리포트 v2)
    "capex": AccountSpec(("CF",), ("ifrs-full_PurchaseOfPropertyPlantAndEquipment",),
                         ("유형자산의취득", "유형자산취득", "유형자산의증가")),
    "intangible_capex": AccountSpec(("CF",), ("ifrs-full_PurchaseOfIntangibleAssets",),
                                    ("무형자산의취득", "무형자산취득", "무형자산의증가")),
    "buyback": AccountSpec(("CF",), ("ifrs-full_PaymentsToAcquireOrRedeemEntitysShares",),
                           ("자기주식의취득", "자기주식취득", "자기주식의매입")),
    "dividends_paid": AccountSpec(("CF",), ("ifrs-full_DividendsPaidClassifiedAsFinancingActivities",),
                                  ("배당금의지급", "배당금지급", "현금배당금의지급", "보통주배당지급")),
    "cash": AccountSpec(("BS",), ("ifrs-full_CashAndCashEquivalents",), ("현금및현금성자산",)),
    "st_fin": AccountSpec(("BS",), ("ifrs-full_ShorttermDepositsNotClassifiedAsCashEquivalents",),
                          ("단기금융상품",)),
    "st_amort": AccountSpec(("BS",), (), ("단기상각후원가금융자산",)),
    "st_fvpl": AccountSpec(("BS",), (), ("단기당기손익-공정가치금융자산", "단기당기손익인식금융자산")),
    "st_borrow": AccountSpec(("BS",), ("ifrs-full_ShorttermBorrowings",), ("단기차입금",)),
    "cur_ltd": AccountSpec(("BS",), ("ifrs-full_CurrentPortionOfLongtermBorrowings",),
                           ("유동성장기부채", "유동성장기차입금", "유동성사채")),
    "bonds": AccountSpec(("BS",), ("ifrs-full_BondsIssued", "dart_BondsIssued"), ("사채",)),
    "lt_borrow": AccountSpec(("BS",), ("ifrs-full_LongtermBorrowings",), ("장기차입금",)),
    "receivables": AccountSpec(("BS",), ("ifrs-full_CurrentTradeReceivables", "dart_ShortTermTradeReceivable"),
                               ("매출채권", "매출채권및기타채권", "매출채권및기타유동채권")),
    "inventory": AccountSpec(("BS",), ("ifrs-full_Inventories",), ("재고자산",)),
}

# 순현금 계산에 쓰는 계정
CASH_LIKE = ("cash", "st_fin", "st_amort", "st_fvpl")
DEBT_LIKE = ("st_borrow", "cur_ltd", "bonds", "lt_borrow")

_LEAD = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+\.|\d+\.|\(\d+\)|[가-하]\.)")
_NOTE = re.compile(r"\((?:주|주석)\s*[\d,\s]+\)")


def normalize_name(name: str) -> str:
    s = re.sub(r"\s+", "", name or "")
    s = _NOTE.sub("", s)
    s = _LEAD.sub("", s)
    return s


@dataclass(frozen=True)
class Found:
    line: FinancialLine
    method: Method


def find_line(snapshot: FinancialSnapshot, metric: str) -> Found | None:
    spec = STANDARD[metric]
    names = {normalize_name(n) for n in spec.names}
    for table in spec.tables:
        rows = [ln for ln in snapshot.lines if ln.sj_div == table]
        by_id = sorted((ln for ln in rows if ln.account_id in spec.ids), key=lambda ln: ln.ord)
        if by_id:
            return Found(by_id[0], "id")
        by_name = sorted((ln for ln in rows if normalize_name(ln.account_nm) in names), key=lambda ln: ln.ord)
        if by_name:
            return Found(by_name[0], "name")
    return None


def has_current_assets(snapshot: FinancialSnapshot) -> bool:
    return any(ln.sj_div == "BS" and ln.account_id == "ifrs-full_CurrentAssets" for ln in snapshot.lines)


def industry_group(snapshot: FinancialSnapshot, induty_code: str | None) -> IndustryGroup:
    """금융 세트: induty_code 앞 두 자리가 64·65·66이면서 재무상태표에 유동자산 줄이 없는 회사."""
    if is_financial_division(induty_code or "") and not has_current_assets(snapshot):
        return "financial"
    return "general"
