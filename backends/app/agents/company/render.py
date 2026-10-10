"""CompanyState → HTML 리포트(기존 프론트가 /report/{corp_code} 응답을 그대로 그린다)."""
from __future__ import annotations

import html
import re
from datetime import datetime

from app.domain.binding import display, render as render_slots, tooltip
from app.domain.dates import KST
from app.domain.ksic import describe_industry
from app.domain.metrics import compute_metrics, period_id
from app.domain.models import ConclusionDraft, Metric

SECTION_TITLES = {
    "stability": "재무 안정성", "profitability": "수익성", "cash_flow": "현금흐름", "strengths": "강점",
    "weaknesses": "약점", "opportunities": "기회", "threats": "위협", "regulatory_risks": "규제 리스크",
    "overall": "종합 판단",
}
METRIC_ORDER = ["revenue", "operating_income", "net_income", "revenue_growth", "op_income_growth",
                "net_income_growth", "gross_margin", "op_margin", "net_margin", "roe", "debt_ratio",
                "current_ratio", "equity_ratio", "cfo", "total_assets", "total_liabilities", "total_equity"]
MARKET = {"Y": "유가증권시장", "K": "코스닥", "N": "코넥스", "E": "기타"}
NO_DATA = '<p class="nodata">데이터를 가져오지 못했습니다.</p>'

CSS = """<style>
.fs-report{font-family:-apple-system,'Noto Sans KR',sans-serif;line-height:1.6;color:#1f2328}
.fs-report table{border-collapse:collapse;width:100%;margin:8px 0 16px}
.fs-report th,.fs-report td{border:1px solid #d0d7de;padding:6px 8px;text-align:left;font-size:14px}
.fs-report th{background:#f6f8fa}.fs-report .num{text-align:right}
.fs-report .metric{border-bottom:1px dotted #57606a;cursor:help}
.fs-report .meta{color:#57606a;font-size:13px}.fs-report .nodata{color:#9a6700}
.fs-report .news{margin:10px 0 14px}.fs-report blockquote{margin:4px 0;padding-left:10px;border-left:3px solid #d0d7de;color:#57606a}
</style>"""


def _e(s: object) -> str:
    return html.escape(str(s)) if s is not None else ""


def _claim_html(text: str, by_slot: dict[str, Metric]) -> str:
    out = render_slots(text, by_slot, as_html=True)
    return re.sub(r"\[(n\d+)\]", lambda m: f'<a href="#{m.group(1)}">[{m.group(1)}]</a>', out)


def margin_trend(state: dict) -> dict | None:
    snap = state.get("snapshot")
    if snap is None:
        return None
    ig = state.get("industry", "general")
    series: list[tuple[str, dict[str, Metric]]] = []
    for h in sorted(state.get("history", []), key=lambda s: s.bsns_year):
        series.append((period_id(h), compute_metrics(h, ig)))
    series.append((period_id(snap), state.get("metrics", {})))
    keys = [k for k in ("gross_margin", "op_margin", "net_margin") if any(k in m for _, m in series)]
    if not keys:
        return None
    labels = {"gross_margin": "매출총이익률", "op_margin": "영업이익률", "net_margin": "순이익률"}
    return {"title": "이익률 추이 (%, 분·반기는 누적)", "x_values": [p for p, _ in series],
            "traces": [{"name": labels[k],
                        "y_values": [float(round(m[k].value, 1)) if k in m and m[k].value is not None else None
                                     for _, m in series]} for k in keys]}


def render_report(state: dict, charts: dict[str, str] | None = None) -> str:
    corp, filing, snap = state["corp"], state.get("filing"), state.get("snapshot")
    metrics: dict[str, Metric] = state.get("metrics", {})
    by_slot = {m.slot_id: m for m in metrics.values()}
    charts = charts or {}
    name = re.sub(r"\(주\)|㈜", "", corp.corp_name).strip()
    parts = [CSS, '<div class="fs-report">', f"<h1>{_e(name)} 기업 분석 보고서</h1>"]
    meta = []
    if filing:
        meta.append(f'기준 보고서: <a href="https://dart.fss.or.kr/dsaf001/main.do?rcpNo={filing.rcept_no}" '
                    f'target="_blank">{_e(filing.report_nm)}</a> (접수 {filing.rcept_dt})')
    if snap:
        meta.append(f"재무 기준: {'연결' if snap.fs_div == 'CFS' else '별도'} · {_e(snap.period_label)}")
    meta.append(f"생성: {datetime.now(KST):%Y-%m-%d %H:%M} KST")
    if state.get("triggers"):
        meta.append("검토 범위: 전자금융거래법령")
    parts.append(f'<p class="meta">{" · ".join(meta)}</p>')

    # I. 기업 개요
    parts.append("<h2>I. 기업 개요</h2><table>")
    rows = [("기업명", corp.corp_name), ("종목코드", corp.stock_code or "비상장"),
            ("시장", MARKET.get(corp.corp_cls or "", corp.corp_cls or "—")),
            ("업종", describe_industry(corp.induty_code or "")), ("결산월", f"{corp.acc_mt}월" if corp.acc_mt else "—")]
    parts += [f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in rows]
    parts.append("</table>")

    # II. 핵심 재무 지표
    parts.append("<h2>II. 핵심 재무 지표</h2>")
    if metrics:
        parts.append("<table><tr><th>지표</th><th>기간</th><th class='num'>값</th></tr>")
        for k in METRIC_ORDER:
            m = metrics.get(k)
            if m is None:
                continue
            val = (f'<span class="metric" title="{_e(tooltip(m))}">{_e(display(m))}</span>'
                   if m.value is not None or m.note else "—")
            parts.append(f"<tr><td>{_e(m.label)}</td><td>{_e(m.period)}</td><td class='num'>{val}</td></tr>")
        parts.append("</table><p class='meta'>값에 마우스를 올리면 산식과 출처(계정명, 열, 접수번호)가 보입니다. "
                     "값이 없는 지표는 공시에서 해당 계정을 찾지 못한 경우입니다.</p>")
    else:
        parts.append(NO_DATA)

    # III. 차트
    parts.append("<h2>III. 추이</h2>")
    if charts.get("price"):
        parts.append("<h3>주가와 지수 (최근 1년)</h3>" + charts["price"])
    if charts.get("margins"):
        parts.append("<h3>이익률 추이</h3>" + charts["margins"])
    if not charts:
        parts.append(NO_DATA)

    # IV. 최근 주요 공시
    parts.append("<h2>IV. 최근 주요 공시 (90일)</h2>")
    disc = state.get("disclosures", [])
    if disc:
        parts.append("<table><tr><th>접수일</th><th>공시</th></tr>")
        parts += [f'<tr><td>{d.rcept_dt}</td><td><a href="{d.url}" target="_blank">{_e(d.report_nm)}</a></td></tr>'
                  for d in disc[:10]]
        parts.append("</table>")
    else:
        parts.append("<p>최근 90일 주요사항보고·거래소공시가 없습니다.</p>")

    # V. 최근 주요 소식
    parts.append("<h2>V. 최근 주요 소식</h2>")
    if state.get("news_note"):
        parts.append(f'<p class="meta">{_e(state["news_note"])}</p>')
    news = state.get("news", [])
    if not news:
        parts.append(NO_DATA if any(e.startswith("news") for e in state.get("errors", [])) else
                     "<p>최근 주요 기사를 찾지 못했습니다.</p>")
    dir_ko = {"positive": "긍정", "negative": "부정", "neutral": "중립"}
    hor_ko = {"short": "1년 내", "long": "장기"}
    for n in news:
        a = n.analysis
        date = n.published_at.strftime("%Y-%m-%d") if n.published_at else ""
        parts.append(f'<div class="news" id="{n.id}"><h4>[{n.id}] <a href="{_e(n.url)}" target="_blank">'
                     f'{_e(n.title)}</a></h4><p class="meta">{_e(n.source)} · {date} · {_e(n.aspect or "")}'
                     f'{" · 유사 보도 " + str(n.cluster_size) + "건" if n.cluster_size > 1 else ""}</p>')
        if a:
            parts.append("<ul>" + "".join(f"<li>{_e(s)}</li>" for s in a.summary) + "</ul>")
            parts.append(f"<p>영향: {dir_ko[a.impact_direction]} · {hor_ko[a.impact_horizon]} · {_e(a.impact_channel)}</p>"
                         f"<blockquote>{_e(a.evidence)}</blockquote>")
        parts.append("</div>")

    # VI. 규제 리스크
    regs = state.get("regulatory", [])
    if regs:
        parts.append("<h2>VI. 규제 검토</h2>")
        for f in regs:
            parts.append(f"<h4>{_e(f.question)}</h4>")
            if not f.answer.answerable:
                parts.append("<p>검토 범위 밖</p>")
                continue
            parts.append("<ul>" + "".join(
                f"<li>{_e(p.text)} <span class='meta'>{_e(', '.join(sorted({c.source for c in p.citations})))}</span></li>"
                for p in f.answer.points) + "</ul>")

    # VII. 종합 결론
    parts.append("<h2>VII. 종합 결론</h2>")
    draft: ConclusionDraft | None = state.get("draft")
    if draft is None:
        parts.append(NO_DATA)
    else:
        for sec in ConclusionDraft.SECTIONS:
            claims = getattr(draft, sec)
            if not claims:
                continue
            parts.append(f"<h3>{SECTION_TITLES[sec]}</h3><ul>" +
                         "".join(f"<li>{_claim_html(c.text, by_slot)}</li>" for c in claims) + "</ul>")
        if draft.monitoring_points:
            parts.append("<h3>모니터링 포인트</h3><ul>" +
                         "".join(f"<li>{_e(p)}</li>" for p in draft.monitoring_points) + "</ul>")
        ver = state.get("verification")
        if ver is not None:
            parts.append(f'<p class="meta">검증: {"통과" if ver.passed else f"주장 {len({i.claim_id for i in ver.issues})}개 수정·삭제"}'
                         f" (재작성 {state.get('attempts', 0)}회)</p>")
    errs = state.get("errors", [])
    if errs:
        parts.append("<h2>데이터 수집 문제</h2><ul>" + "".join(f"<li>{_e(e)}</li>" for e in errs) + "</ul>")
    parts.append("<p class='meta'>이 리포트는 투자 권유가 아니며, 재무 수치는 OpenDART 공시에서 계산했습니다.</p></div>")
    return "\n".join(parts)
