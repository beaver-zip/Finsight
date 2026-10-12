"""report_companies(16개 = 8층 × 2)와 report_gold(주요계정 API 자동 대조 정답), news_labels 대상 8개 기업.

층: 대형주, 코스닥, 지주사, 금융사, 연결 없는 회사(별도 재무제표만), 12월 외 결산, 적자 기업, 정정 공시.
층마다 해당 상장사에서 무작위로 뽑고, 값싼 속성(시장·업종·결산월)으로 다른 층과 겹치면 다시 뽑는다.
OpenDART 요청은 이 스크립트 전체에서 MAX_DART건을 넘지 않는다(IP 차단 방지).

report_gold: 각 회사의 2025 사업보고서 주요계정(fnlttSinglAcnt) 매출액·영업이익·당기순이익·자본총계·자산총계.
v2 리포트와 독립된 API·식이고, 사람 검수 없이 쓴다(2026-10-12 결정).
사용: cd backends && PYTHONPATH=. python -m eval.build.report_companies
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from decimal import Decimal

import httpx

from app.core.dart.client import DartClient, DartNoData
from app.core.dart.financials import to_amount
from app.core.peers import load_universe
from eval.common import DATASETS, assign_splits, rng, save_jsonl

log = logging.getLogger("report_companies")
MAX_DART = 60
YEAR = 2025
GOLD = {"매출액": "revenue", "영업이익": "operating_income", "당기순이익": "net_income", "자본총계": "total_equity",
        "자산총계": "total_assets"}


class Budget:
    def __init__(self, n: int):
        self.left = n

    def take(self) -> None:
        if self.left <= 0:
            raise RuntimeError("OpenDART 요청 상한에 닿았다")
        self.left -= 1


def is_fin(u: dict) -> bool:
    return (u.get("induty_code") or "")[:2] in ("64", "65", "66") and not (u.get("induty_code") or "").startswith("64992")


def is_holding(u: dict) -> bool:
    return (u.get("induty_code") or "").startswith("64992") and "금융" not in u["name"]


def cheap_strata(u: dict, big: set[str]) -> set[str]:
    s = set()
    if u["stock_code"] in big:
        s.add("대형주")
    if u.get("corp_cls") == "K":
        s.add("코스닥")
    if is_holding(u):
        s.add("지주사")
    if is_fin(u):
        s.add("금융사")
    if u.get("acc_mt") and int(u["acc_mt"]) != 12:
        s.add("12월 외 결산")
    return s


async def key_accounts(dart: DartClient, b: Budget, corp_code: str) -> dict | None:
    b.take()
    try:
        d = await dart.json("fnlttSinglAcnt", corp_code=corp_code, bsns_year=str(YEAR), reprt_code="11011")
    except DartNoData:
        return None
    rows = d.get("list", [])
    out: dict = {"rcept_no": rows[0]["rcept_no"] if rows else None, "CFS": {}, "OFS": {}}
    for r in rows:
        k = GOLD.get(re.sub(r"\s*\((?:손실|이익)\)\s*$", "", (r.get("account_nm") or "").strip()))
        if k:
            out[r["fs_div"]][k] = to_amount(r.get("thstrm_amount"))
    return out


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    r = rng()
    uni = [u for u in load_universe() if u.get("mcap_approx") and u.get("stock_code")]
    big = {u["stock_code"] for u in sorted(uni, key=lambda u: -u["mcap_approx"])[:30]}
    b = Budget(MAX_DART)
    picks: dict[str, list[dict]] = {}
    accounts: dict[str, dict] = {}
    used: set[str] = set()

    def pool(pred, own: str) -> list[dict]:
        return [u for u in uni if u["corp_code"] not in used and pred(u) and cheap_strata(u, big) <= {own}]

    simple = {"대형주": lambda u: u["stock_code"] in big, "코스닥": lambda u: u.get("corp_cls") == "K",
              "지주사": is_holding, "금융사": is_fin,
              "12월 외 결산": lambda u: bool(u.get("acc_mt")) and int(u["acc_mt"]) != 12}
    for name, pred in simple.items():
        cand = pool(pred, name)
        picks[name] = r.sample(cand, 2)
        used |= {u["corp_code"] for u in picks[name]}

    async with httpx.AsyncClient(timeout=20) as http:
        dart = DartClient(http, concurrency=1)
        # 정정 공시: 최근 3개월 정기보고서 중 [기재정정]
        end = date.today()
        corrected: set[str] = set()
        for page in (1, 2, 3):
            b.take()
            d = await dart.json("list", bgn_de=(end - timedelta(days=89)).strftime("%Y%m%d"), end_de=end.strftime("%Y%m%d"),
                                pblntf_ty="A", page_no=str(page), page_count="100")
            corrected |= {x["corp_code"] for x in d.get("list", []) if x["report_nm"].startswith("[기재정정]")}
            if page >= int(d.get("total_page", 1)):
                break
        cand = [u for u in pool(lambda u: u["corp_code"] in corrected, "정정 공시")]
        picks["정정 공시"] = r.sample(cand, min(2, len(cand)))
        used |= {u["corp_code"] for u in picks["정정 공시"]}
        # 연결 없는 회사·적자 기업: 주요계정을 한 곳씩 확인
        for name in ("연결 없는 회사", "적자 기업"):
            picks[name] = []
            for u in r.sample(pool(lambda u: True, name), 80):
                if len(picks[name]) == 2 or b.left <= 16:
                    break
                acc = await key_accounts(dart, b, u["corp_code"])
                if acc is None:
                    continue
                accounts[u["corp_code"]] = acc
                ofs_only = not acc["CFS"] and bool(acc["OFS"])
                ni = (acc["CFS"] or acc["OFS"]).get("net_income")
                ok = ofs_only if name == "연결 없는 회사" else (ni is not None and ni < 0 and not ofs_only)
                if ok:
                    picks[name].append(u)
                    used.add(u["corp_code"])
        # 정답: 나머지 회사의 주요계정
        rows, gold = [], []
        for stratum, us in picks.items():
            for u in us:
                acc = accounts.get(u["corp_code"]) or await key_accounts(dart, b, u["corp_code"])
                rows.append({"id": u["corp_code"], "corp_code": u["corp_code"], "stock_code": u["stock_code"],
                             "name": u["name"], "stratum": stratum, "acc_mt": u.get("acc_mt")})
                if not acc:
                    continue
                fs = "CFS" if acc["CFS"] else "OFS"
                for metric, v in acc[fs].items():
                    if v is not None:
                        gold.append({"id": f"{u['corp_code']}:{metric}:{YEAR}FY", "corp_code": u["corp_code"],
                                     "rcept_no": acc["rcept_no"], "metric": metric, "period": f"{YEAR}FY",
                                     "fs_div": fs, "value": str(v), "verified_by": "fnlttSinglAcnt"})
    rows = assign_splits(rows, strata=lambda x: x["stratum"], dev_ratio=0.5)
    save_jsonl(DATASETS / "report_companies.v1.jsonl", rows)
    save_jsonl(DATASETS / "report_gold.v1.jsonl", gold)
    # news_labels 대상: 층마다 1개(test 쪽 회사)
    news = [next(x for x in rows if x["stratum"] == s and x["split"] == "test") for s in picks if picks[s]]
    save_jsonl(DATASETS / "news_companies.v1.jsonl", [{"id": x["corp_code"], **x} for x in news])
    log.info("기업 %d, 정답 %d, 뉴스 대상 %d, OpenDART 남은 요청 %d", len(rows), len(gold), len(news), b.left)
    for x in rows:
        log.info("  %s %s %s %s", x["stratum"], x["name"], x["stock_code"], x["split"])




async def fill(stratum: str = "적자 기업", n: int = 2, max_dart: int = 30) -> None:
    """비어 있는 층만 마저 채운다(주요계정 확인이 필요한 층). 기존 파일에 덧붙인다."""
    from eval.common import load_jsonl

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    rows = load_jsonl(DATASETS / "report_companies.v1.jsonl")
    gold = load_jsonl(DATASETS / "report_gold.v1.jsonl")
    have = [x for x in rows if x["stratum"] == stratum]
    r = rng(20261013)
    uni = [u for u in load_universe() if u.get("mcap_approx") and u.get("stock_code")]
    big = {u["stock_code"] for u in sorted(uni, key=lambda u: -u["mcap_approx"])[:30]}
    used = {x["corp_code"] for x in rows}
    b = Budget(max_dart)
    async with httpx.AsyncClient(timeout=20) as http:
        dart = DartClient(http, concurrency=1)
        for u in r.sample([u for u in uni if u["corp_code"] not in used and not cheap_strata(u, big)], 200):
            if len(have) >= n or b.left <= 0:
                break
            acc = await key_accounts(dart, b, u["corp_code"])
            if not acc or not acc["CFS"]:
                continue
            ni = acc["CFS"].get("net_income")
            if ni is None or ni >= 0:
                continue
            row = {"id": u["corp_code"], "corp_code": u["corp_code"], "stock_code": u["stock_code"], "name": u["name"],
                   "stratum": stratum, "acc_mt": u.get("acc_mt")}
            have.append(row)
            rows.append(row)
            for metric, v in acc["CFS"].items():
                if v is not None:
                    gold.append({"id": f"{u['corp_code']}:{metric}:{YEAR}FY", "corp_code": u["corp_code"],
                                 "rcept_no": acc["rcept_no"], "metric": metric, "period": f"{YEAR}FY",
                                 "fs_div": "CFS", "value": str(v), "verified_by": "fnlttSinglAcnt"})
    rows = assign_splits(rows, strata=lambda x: x["stratum"], dev_ratio=0.5)
    save_jsonl(DATASETS / "report_companies.v1.jsonl", rows)
    save_jsonl(DATASETS / "report_gold.v1.jsonl", gold)
    news = load_jsonl(DATASETS / "news_companies.v1.jsonl")
    if not any(x["stratum"] == stratum for x in news):
        t = next((x for x in rows if x["stratum"] == stratum and x["split"] == "test"), None)
        if t:
            news.append({**t})
            save_jsonl(DATASETS / "news_companies.v1.jsonl", news)
    log.info("%s %d개: %s, 남은 요청 %d", stratum, len(have), [x["name"] for x in have], b.left)


async def regold() -> None:
    """정답(report_gold)만 다시 받는다(계정명 매칭을 고친 뒤)."""
    from eval.common import load_jsonl

    rows = load_jsonl(DATASETS / "report_companies.v1.jsonl")
    gold = []
    b = Budget(len(rows))
    async with httpx.AsyncClient(timeout=20) as http:
        dart = DartClient(http, concurrency=1)
        for x in rows:
            acc = await key_accounts(dart, b, x["corp_code"])
            if not acc:
                continue
            fs = "CFS" if acc["CFS"] else "OFS"
            gold += [{"id": f"{x['corp_code']}:{m}:{YEAR}FY", "corp_code": x["corp_code"], "rcept_no": acc["rcept_no"],
                      "metric": m, "period": f"{YEAR}FY", "fs_div": fs, "value": str(v), "verified_by": "fnlttSinglAcnt"}
                     for m, v in acc[fs].items() if v is not None]
    save_jsonl(DATASETS / "report_gold.v1.jsonl", gold)
    print("정답", len(gold))


if __name__ == "__main__":
    import sys

    asyncio.run(fill() if "--fill" in sys.argv else regold() if "--regold" in sys.argv else main())
