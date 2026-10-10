"""지표 계산. 모두 Decimal로 계산하고 반올림은 표시 단계(binding.display)에서만 한다."""
from __future__ import annotations

from decimal import Decimal

from app.domain.accounts import find_line, has_current_assets
from app.domain.ksic import is_financial_division
from app.domain.models import FinancialSnapshot, IndustryGroup, Metric, SourceRef

_PERIOD = {"11013": "Q1", "11012": "H1", "11014": "Q3", "11011": "FY"}
_MONTHS = {"11013": 3, "11012": 6, "11014": 9, "11011": 12}
HUNDRED = Decimal(100)

# 분·반기 현금흐름표의 누적값 열은 설계서 미확정 사양. 확인 전까지 thstrm_amount를 먼저 쓴다.
CF_QUARTER_COLUMNS = ("thstrm_amount", "thstrm_add_amount")


def period_id(snapshot: FinancialSnapshot) -> str:
    return f"{snapshot.bsns_year}{_PERIOD[snapshot.reprt_code]}"


def industry_group(snapshot: FinancialSnapshot, induty_code: str | None) -> IndustryGroup:
    """금융 세트: induty_code 앞 두 자리가 64·65·66이면서 재무상태표에 유동자산 줄이 없는 회사."""
    if is_financial_division(induty_code or "") and not has_current_assets(snapshot):
        return "financial"
    return "general"


def _columns(snapshot: FinancialSnapshot, sj: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(당기 열 후보, 비교 열 후보)."""
    annual = snapshot.reprt_code == "11011"
    if sj in ("IS", "CIS"):
        return (("thstrm_amount",), ("frmtrm_amount",)) if annual else \
            (("thstrm_add_amount",), ("frmtrm_add_amount",))
    if sj == "CF" and not annual:
        return CF_QUARTER_COLUMNS, ()
    return ("thstrm_amount",), ("frmtrm_amount",)


class _Values:
    def __init__(self, snapshot: FinancialSnapshot):
        self.s = snapshot
        self.found: dict[str, tuple[Decimal | None, Decimal | None, SourceRef | None, SourceRef | None]] = {}

    def get(self, account: str):
        if account in self.found:
            return self.found[account]
        f = find_line(self.s, account)
        result: tuple = (None, None, None, None)
        if f is not None:
            cur_cols, cmp_cols = _columns(self.s, f.line.sj_div)
            cur = cmp_ = None
            cur_ref = cmp_ref = None
            for col in cur_cols:
                v = getattr(f.line, col)
                if v is not None:
                    cur, cur_ref = v, self._ref(f.line, col, v)
                    break
            for col in cmp_cols:
                v = getattr(f.line, col)
                if v is not None:
                    cmp_, cmp_ref = v, self._ref(f.line, col, v)
                    break
            result = (cur, cmp_, cur_ref, cmp_ref)
        self.found[account] = result
        return result

    def _ref(self, line, col, amount) -> SourceRef:
        return SourceRef(rcept_no=self.s.rcept_no, sj_div=line.sj_div, account_id=line.account_id,
                         account_nm=line.account_nm, column=col, amount=amount)


LABELS = {
    "revenue": "매출액", "operating_income": "영업이익", "net_income": "순이익", "total_assets": "자산총계",
    "total_liabilities": "부채총계", "total_equity": "자본총계",
    "revenue_growth": "매출 성장률", "op_income_growth": "영업이익 증감률", "net_income_growth": "순이익 증감률",
    "gross_margin": "매출총이익률", "op_margin": "영업이익률", "net_margin": "순이익률", "debt_ratio": "부채비율",
    "current_ratio": "유동비율", "equity_ratio": "자기자본비율", "roe": "ROE", "cfo": "영업활동현금흐름",
}
GENERAL_ONLY = {"gross_margin", "debt_ratio", "current_ratio", "cfo"}
FINANCIAL_ONLY = {"equity_ratio"}


def compute_metrics(snapshot: FinancialSnapshot, industry: IndustryGroup) -> dict[str, Metric]:
    v = _Values(snapshot)
    pid = period_id(snapshot)
    out: dict[str, Metric] = {}

    def put(mid: str, value: Decimal | None, unit: str, formula: str, refs: list, note: str | None = None):
        if (mid in GENERAL_ONLY and industry != "general") or (mid in FINANCIAL_ONLY and industry != "financial"):
            return
        out[mid] = Metric(id=mid, label=LABELS[mid], period=pid, value=value, unit=unit, formula=formula,
                          inputs=[r for r in refs if r is not None], note=note)

    def cur(a):
        return v.get(a)[0]

    def ref(a):
        return v.get(a)[2]

    # 금액 지표(사실표·평가용)
    for a in ("revenue", "operating_income", "net_income", "total_assets", "total_liabilities", "total_equity"):
        put(a, cur(a), "KRW", a, [ref(a)])

    # 성장률
    rc, rp, rcr, rpr = v.get("revenue")
    growth = (rc / rp - 1) * HUNDRED if rc is not None and rp is not None and rp > 0 else None
    put("revenue_growth", growth, "%", "revenue / revenue_prev - 1", [rcr, rpr])
    for mid, a in (("op_income_growth", "operating_income"), ("net_income_growth", "net_income")):
        c, p, cr, pr = v.get(a)
        value, note = signed_growth(c, p)
        put(mid, value, "%", f"{a} / {a}_prev - 1", [cr, pr], note)

    # 이익률
    rev = cur("revenue")
    for mid, a in (("gross_margin", "gross_profit"), ("op_margin", "operating_income"), ("net_margin", "net_income")):
        num = cur(a)
        val = num / rev * HUNDRED if num is not None and rev not in (None, 0) else None
        put(mid, val, "%", f"{a} / revenue", [ref(a), ref("revenue")])

    # 재무 구조
    liab, eq, assets = cur("total_liabilities"), cur("total_equity"), cur("total_assets")
    if liab is not None and eq is not None and eq <= 0:
        put("debt_ratio", None, "%", "total_liabilities / total_equity", [ref("total_liabilities"), ref("total_equity")],
            "자본잠식")
    else:
        put("debt_ratio", liab / eq * HUNDRED if liab is not None and eq else None, "%",
            "total_liabilities / total_equity", [ref("total_liabilities"), ref("total_equity")])
    ca, cl = cur("current_assets"), cur("current_liabilities")
    put("current_ratio", ca / cl * HUNDRED if ca is not None and cl else None, "%",
        "current_assets / current_liabilities", [ref("current_assets"), ref("current_liabilities")])
    put("equity_ratio", eq / assets * HUNDRED if eq is not None and assets else None, "%",
        "total_equity / total_assets", [ref("total_equity"), ref("total_assets")])

    # ROE: 연결은 지배주주 순이익 / 기말 지배주주 자본, 별도는 순이익 / 기말 자본총계
    if snapshot.fs_div == "CFS":
        ni, e, refs, formula = cur("net_income_owners"), cur("equity_owners"), \
            [ref("net_income_owners"), ref("equity_owners")], "net_income_owners / equity_owners"
    else:
        ni, e, refs, formula = cur("net_income"), eq, [ref("net_income"), ref("total_equity")], \
            "net_income / total_equity"
    note = None
    roe = None
    if ni is not None and e is not None and e > 0:
        months = _MONTHS[snapshot.reprt_code]
        if months != 12:
            ni = ni * 12 / months
            note = "연환산"
            formula += " × 12 / 누적 개월 수"
        roe = ni / e * HUNDRED
    put("roe", roe, "%", formula, refs, note)

    put("cfo", cur("cfo"), "KRW", "cfo", [ref("cfo")])
    return out


def signed_growth(cur: Decimal | None, prev: Decimal | None) -> tuple[Decimal | None, str | None]:
    """영업이익·순이익 증감률의 부호 규칙."""
    if cur is None or prev is None:
        return None, None
    if prev <= 0 and cur > 0:
        return None, "흑자 전환"
    if prev > 0 and cur <= 0:
        return None, "적자 전환"
    if prev <= 0 and cur <= 0:
        return None, "적자 지속"
    return (cur / prev - 1) * HUNDRED, None
