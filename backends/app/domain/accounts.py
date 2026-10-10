"""계정 매핑: 표준 지표마다 account_id 후보 → 계정명 동의어 순으로 줄을 찾는다. 못 찾으면 None(0으로 채우지 않는다)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.domain.models import FinancialLine, FinancialSnapshot

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
                                     ("지배기업소유주지분", "지배기업의소유주지분")),
    "total_assets": AccountSpec(("BS",), ("ifrs-full_Assets",), ("자산총계",)),
    "current_assets": AccountSpec(("BS",), ("ifrs-full_CurrentAssets",), ("유동자산",)),
    "total_liabilities": AccountSpec(("BS",), ("ifrs-full_Liabilities",), ("부채총계",)),
    "current_liabilities": AccountSpec(("BS",), ("ifrs-full_CurrentLiabilities",), ("유동부채",)),
    "total_equity": AccountSpec(("BS",), ("ifrs-full_Equity",), ("자본총계",)),
    "equity_owners": AccountSpec(("BS",), ("ifrs-full_EquityAttributableToOwnersOfParent",),
                                 ("지배기업소유주지분", "지배기업의소유주지분")),
    "cfo": AccountSpec(("CF",), ("ifrs-full_CashFlowsFromUsedInOperatingActivities",),
                       ("영업활동현금흐름", "영업활동으로인한현금흐름")),
}

_LEAD = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.|\d+\.|\(\d+\)|[가-하]\.)")
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
