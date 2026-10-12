"""리포트 스위트(R1·R2): report_companies 기업마다 리포트 그래프를 돌린다.

- 수치(R1·R2 공통, 코드 계산): 연간 표(매출액·영업이익·지배순이익)를 report_gold(주요계정 API, 2025 사업연도)와 대조.
  상대오차 0.5% 이내면 일치. 순이익은 정답이 당기순이익(비지배 포함)이라 별도 재무제표 회사만 비교한다.
  일치율 = 일치 ÷ 정답 항목, 오답률 = 0.5% 초과 ÷ 낸 값, 커버리지 = 낸 값 ÷ 정답 항목.
- 본문: R1 = 첫 초안(검증 전), R2 = 검증·재작성 뒤 최종(실패 주장은 빼거나 표시). 추적 불가 숫자율·검증 실패율은 첫 검증 결과,
  주장 지지율은 judge(Claude 외 모델)가 근거 블록(사실표 값·기사·조문)으로 판정한다.
- OpenDART 요청이 회사당 100~200건이라 한 번에 max_new개 회사만 새로 돌린다(나머지는 다음 실행).
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

import httpx

from app.agents.company.graph import build_company_graph
from app.agents.company.nodes import evidence_of
from app.agents.verifier import _evidence_block, finalize
from app.core import budget
from app.core.dart import corp as dart_corp
from app.core.dart.client import DartClient
from eval import judge
from eval.common import dataset

ROWS = {"revenue": "매출액", "operating_income": "영업이익", "net_income": "지배순이익"}
TOL = 0.005


def parse_cell(s: str, div: float) -> float | None:
    t = (s or "").replace(",", "").strip()
    try:
        return float(t) * div
    except ValueError:
        return None


def numeric_scores(data: dict | None, fs_div: str | None, gold: list[dict]) -> dict:
    if not data:
        return {"num_match": 0.0, "num_coverage": 0.0, "num_error": None}
    t = data["tables"].get("annual") or {}
    heads = t.get("heads", [])
    div = 1e9 if data.get("table_unit") == "십억원" else 1e8
    rows = {r["head"]: r["cells"] for r in t.get("rows", [])}
    want = [g for g in gold if g["metric"] in ROWS and (g["metric"] != "net_income" or g["fs_div"] == "OFS")]
    match = given = wrong = 0
    for g in want:
        year = g["period"][:4]
        label = ROWS[g["metric"]] if g["metric"] != "net_income" else ("지배순이익" if "지배순이익" in rows else "순이익")
        cells = rows.get(label)
        if not cells or year not in heads:
            continue
        v = parse_cell(cells[heads.index(year)], div)
        if v is None:
            continue
        given += 1
        gv = float(Decimal(g["value"]))
        if gv and abs(v - gv) / abs(gv) <= TOL:
            match += 1
        else:
            wrong += 1
    n = len(want)
    return {"num_match": match / n if n else None, "num_coverage": given / n if n else None,
            "num_error": wrong / given if given else None}


async def support_rate(claims, ev) -> float | None:
    pairs = []
    for c in claims:
        b = _evidence_block(c, ev)
        evid = json.dumps({k: b[k] for k in ("metrics", "news", "legal") if b.get(k)}, ensure_ascii=False)
        pairs.append((c.text, evid))
    if not pairs:
        return None
    sup = []
    for i in range(0, len(pairs), 8):           # judge 한 번에 8개씩
        part = await judge.supported(pairs[i:i + 8])
        if part is None:
            return None
        sup += part
    return sum(sup) / len(sup)


async def run(items: list[dict], p: dict[str, Any]) -> tuple[list[dict], dict]:
    gold_all = dataset("report_gold")
    graph = build_company_graph()
    out = []
    new = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        dart = DartClient(http, concurrency=2)
        for it in items:
            if new >= p.get("max_new", 4):
                break
            usage = budget.start_request()
            t0 = time.perf_counter()
            corp = await dart_corp.get_profile(dart, it["corp_code"])
            state: dict = {}
            first_draft = first_ver = None
            try:
                async for upd in graph.astream({"corp": corp, "errors": []}, stream_mode="updates",
                                               config={"configurable": {"dart": dart, "http": http,
                                                                        "legal_runner": None},
                                                       "recursion_limit": 30}):
                    for node, u in upd.items():
                        if not isinstance(u, dict):
                            continue
                        if node == "write" and first_draft is None:
                            first_draft = u.get("draft")
                        if node == "verify" and first_ver is None:
                            first_ver = u.get("verification")
                        errs = u.get("errors")
                        state.update({k: v for k, v in u.items() if k != "errors"})
                        state.setdefault("errors", []).extend(errs or [])
            except Exception as e:  # noqa: BLE001
                out.append({"id": it["id"], "name": it["name"], "error": f"{type(e).__name__}: {e}", "scores": {}})
                continue
            new += 1
            data = state.get("data")
            col = state.get("collected")
            fs = col.quarters[-1].fs_div if col is not None and col.quarters else None
            sc: dict[str, Any] = numeric_scores(data.to_json() if data else None, fs,
                                                [g for g in gold_all if g["corp_code"] == it["corp_code"]])
            if first_draft is not None and first_ver is not None:
                ev = evidence_of(state)
                claims1 = first_draft.claims()
                bad = {i.claim_id for i in first_ver.issues if i.type != "style"}
                untr = {i.claim_id for i in first_ver.issues if i.type == "untraceable_number"}
                sc["untraceable_rate_R1"] = len(untr) / len(claims1)
                sc["verify_fail_rate_R1"] = len(bad) / len(claims1)
                sc["support_R1"] = await support_rate(claims1, ev)
                final = state.get("draft")
                ver = state.get("verification")
                claims2 = finalize(final.claims(), ver) if final is not None and ver is not None and not ver.passed \
                    else (final.claims() if final is not None else [])
                sc["support_R2"] = await support_rate(claims2, ev)
                sc["claims_dropped_R2"] = 1 - len(claims2) / len(final.claims()) if final is not None else None
                types: dict[str, int] = {}
                for i in first_ver.issues:
                    types[i.type] = types.get(i.type, 0) + 1
            else:
                types = {}
            out.append({"id": it["id"], "name": it["name"], "stratum": it["stratum"], "fs_div": fs, "scores": sc,
                        "issue_types_R1": types, "errors": state.get("errors"),
                        "latency_s": round(time.perf_counter() - t0, 2), "cost_usd": round(usage.cost_usd, 6)})
    return out, {"judge": judge.judge_model()[0] + (" (임시 judge)" if judge.judge_model()[1] else ""),
                 "note": "R1=첫 초안(검증 전), R2=검증·재작성 뒤. 수치는 2025 사업연도 주요계정과 대조"}
