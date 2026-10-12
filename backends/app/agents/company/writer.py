"""리포트 v2 문장: 컨센서스 추출, 본문 작성, 재작성, 최종 정리.

- 숫자는 analysis가 만든 사실표의 {{id}} 슬롯으로만 쓴다. 기사에서 온 사실은 [n1]을 붙이고 기사 표기 그대로 쓴다.
- 컨센서스는 기사에서 LLM이 뽑되, 인용문이 본문에 그대로 있고 금액이 인용문에서 다시 읽힐 때만 쓴다.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel

from app.agents.company.analysis import Consensus, ReportData
from app.agents.news.pipeline import evidence_ok, normalize
from app.core import llm
from app.domain.binding import display
from app.domain.models import Block, Headline, NewsItem, ReportDraft, VerificationResult

log = logging.getLogger(__name__)

# ---------------- 컨센서스
CONS_WORDS = ("컨센서스", "전망치", "추정치", "예상치", "시장 기대", "평균 전망")
PROVIDERS = ("에프앤가이드", "FnGuide", "연합인포맥스", "인포맥스", "와이즈리포트", "블룸버그", "Bloomberg", "LSEG", "레피니티브")


def parse_krw(s: str | None) -> float | None:
    """'106조29억원', '1조2,000억 원', '3,500억원', '2.5조원' → 원. 못 읽으면 None."""
    if not s:
        return None
    t = s.replace(" ", "")
    m = re.fullmatch(r"(?:(\d[\d,]*(?:\.\d+)?)조)?(?:(\d[\d,]*(?:\.\d+)?)억)?(?:(\d[\d,]*)만)?원?", t)
    if not m or not any(m.groups()):
        return None
    jo, eok, man = (float(g.replace(",", "")) if g else 0.0 for g in m.groups())
    return jo * 1e12 + eok * 1e8 + man * 1e4


class _Cons(BaseModel):
    found: bool
    news_id: str | None
    period: str | None            # "3Q26"
    provider: str | None          # 집계 기관(에프앤가이드 등). 기사에 없으면 null
    revenue_text: str | None      # 매출액 컨센서스, 기사 표기 그대로("200조2847억원")
    op_text: str | None           # 영업이익 컨센서스, 기사 표기 그대로
    quote: str | None             # 금액이 들어 있는 문장을 본문에서 글자 그대로


CONS_SYSTEM = """기사에서 지정한 분기 실적의 시장 컨센서스(증권사 전망치 평균)를 찾는다.
- 지정한 분기와 다른 기간(연간, 다른 분기)의 전망치나 특정 증권사 한 곳의 추정치는 컨센서스가 아니다.
- 금액은 기사 표기 그대로 옮긴다(예: "106조29억원"). 단위를 바꾸거나 계산하지 않는다.
- quote는 금액이 들어 있는 본문 문장을 한 글자도 바꾸지 말고 옮긴다. 매출과 영업이익이 다른 문장에 있으면 영업이익 문장.
- 찾지 못하면 found=false."""


def _excerpt(text: str, words: tuple[str, ...], width: int = 700, limit: int = 2400) -> str:
    spans = []
    for w in words:
        for m in re.finditer(re.escape(w), text):
            spans.append((max(0, m.start() - width // 2), min(len(text), m.end() + width // 2)))
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = " … ".join(text[a:b] for a, b in merged)
    return out[:limit]


async def extract_consensus(period: str, q_label: str, actual_op: float | None, news: list[NewsItem],
                            texts: dict[str, str]) -> Consensus | None:
    """period: '3Q26', q_label: '3분기'. 본문에 컨센서스 단어와 분기 표기가 함께 있는 기사만 본다(최대 4건)."""
    cands = [n for n in news if any(w in texts.get(n.id, "") for w in CONS_WORDS) and q_label in texts.get(n.id, "")]
    if not cands:
        return None
    docs = [{"id": n.id, "title": n.title, "date": n.published_at.date().isoformat() if n.published_at else None,
             "excerpt": _excerpt(texts[n.id], CONS_WORDS)} for n in cands[:4]]
    try:
        out = await llm.structured(_Cons, [{"role": "user", "content": json.dumps(
            {"분기": f"{period} ({q_label})", "기사": docs}, ensure_ascii=False)}],
            system=CONS_SYSTEM, effort="low", task="company.consensus")
    except Exception as e:  # noqa: BLE001
        log.info("컨센서스 추출 실패: %s", e)
        return None
    if not out.found or not out.news_id or out.news_id not in texts or not out.quote:
        return None
    body = texts[out.news_id]
    if not evidence_ok(out.quote, body):
        log.info("컨센서스 인용문이 본문에 없다: %s", out.quote)
        return None
    mp = re.search(r"[1-4]Q\d{2}", (out.period or "").upper())
    if mp and mp.group(0) != period:
        return None
    qn = normalize(out.quote)
    rev = parse_krw(out.revenue_text) if out.revenue_text and normalize(out.revenue_text) in normalize(body) else None
    op = parse_krw(out.op_text) if out.op_text and normalize(out.op_text) in qn else None
    if op is None:
        return None
    if actual_op and actual_op > 0 and not (0.5 < op / actual_op < 2):     # 다른 기간·단위를 잘못 읽은 경우
        log.info("컨센서스가 실제와 너무 다르다: %s vs %s", op, actual_op)
        return None
    n = next(x for x in news if x.id == out.news_id)
    from app.domain.sources import outlet_name

    provider = next((p for p in PROVIDERS if p in (out.provider or "") and p in body), None)
    return Consensus(period=period, revenue=rev, operating_income=op, provider=provider, news_id=out.news_id,
                     outlet=outlet_name(n.url), quote=out.quote)


# ---------------- 본문 작성
WRITER_SYSTEM = """너는 한국 증권사 리서치센터의 기업 분석 애널리스트다. 함께 준 자료(사실표, 차트·표 목록, 기사, 공시)만으로 리포트 본문을 쓴다.

[문체]
- 개조식(음슴체)으로 쓴다: "~했음", "~임", "~늘었음", "~예정", "~로 추정". 문장을 "~다"로 끝내지 않는다.
- 블록 = 소제목(head) 한 줄 + 본문(text) 2~4문장. 소제목은 그 블록의 결론을 담은 짧은 주장(30자 이내)이다.
- 맺음말·해석용 상투어를 쓰지 않는다: "~구조", "~구간", "~셈", "~가능성", "~로 판단됨", "~할 것으로 보임", "주목할 필요", "지켜볼 필요", "시사함", "의미함". 사실과, 근거가 있을 때의 원인만 쓴다.
- 자료나 작성 과정에 대한 말("본 리포트는", "제공된 자료에 따르면", "사실표")을 쓰지 않는다.
- 본문 블록끼리 같은 숫자를 되풀이하지 않는다(요약 summary는 예외).

[숫자]
- 재무·시세 숫자는 직접 쓰지 않고 사실표의 id를 {{id}}로 쓴다. 슬롯 자리에는 사실표의 display가 그대로 들어간다.
  예: "영업이익은 {{op.3Q26P}}으로 컨센서스를 {{op_vs_cons.3Q26P}}" → "영업이익은 107.4조원으로 컨센서스를 1.3% 상회"
- display에 증가·감소·상회·하회·상승·하락이 들어 있으면 슬롯 앞뒤에 같은 뜻의 말을 다시 쓰지 않는다. +·− 부호가 붙은 display(예: "+20.0%")는 "QoQ {{...}}"처럼 괄호나 비교 표현 안에서 쓴다.
- 조사는 display의 마지막 글자에 맞춘다("107.4조원으로", "55.1%로", "3.04배로").
- 연도·날짜·분기 표기(2016년, 10월 29일, 3Q26, 1~3분기)는 그대로 써도 된다.
- 기사에서 온 사실·숫자는 문장 끝에 [n1]처럼 기사 id를 붙이고, 숫자는 기사 표기 그대로 쓴다. 사실표 숫자만 쓴 문장에는 기사 id를 붙이지 않는다.
- 사실표·기사에 없는 숫자는 쓰지 않는다. 직접 계산한 값도 쓰지 않는다.
- metric_ids에는 그 블록(또는 제목)에서 쓴 슬롯 id와 근거로 삼은 사실표 id를, news_ids에는 인용한 기사 id를 넣는다.

[내용]
- 투자 의견(매수·매도·보유), 목표주가, 비중 확대·축소, 앞으로의 실적·주가 전망을 쓰지 않는다. 증권사·업계의 추정과 의견은 기사 id를 붙여 누가 그렇게 봤는지로만 전한다.
- 원인 설명은 기사나 부문 숫자로 뒷받침될 때만 쓴다.
- 자기주식 취득은 목적(bb_purpose)을 확인해 쓴다. 목적이 소각이 아니면 소각한다고 쓰지 않는다.
- 소제목은 바로 뒤에 오는 차트의 제목과 같은 말을 쓰지 않는다(차트 목록 참고).

[구성]
- title: 리포트 제목. 지금 이 회사를 요약하는 주장 한 줄(25자 안팎). 슬롯을 쓰지 않는다. metric_ids에 근거 id.
- summary: 정확히 4개. 1) 최근 분기 실적 2) 이익의 원천(부문·제품·가격) 3) 밸류에이션 4) 현금흐름·주주환원. 자료가 없는 주제는 다른 핵심 사실로 바꾼다.
- 절 제목(earnings_head, valuation_head, cash_head, market_head): 화면에서 "실적: ", "밸류에이션: ", "현금흐름과 주주환원: ", "주가와 뉴스: " 뒤에 붙는 주장 한 줄(20자 안팎). 절 이름은 쓰지 않는다(예: "이익은 기대 이상, 매출은 기대 이하"). 슬롯을 쓰지 않고 근거 id를 metric_ids에.
- earnings_a(1~2): 최근 분기 실적, 컨센서스 대비, 증감. 뒤에 '분기 영업이익' 차트가 온다.
- earnings_b(1~2): 이익의 원인(부문별 실적, 가격·수요, 비용). 뒤에 '부문' 차트와 분기 표가 온다.
- valuation_a(1~2): PBR의 역사적 위치와 최근 변화. 뒤에 'PBR 밴드' 차트.
- valuation_b(1~2): PER(기준 이익에 따른 차이), 동종 업종 비교. 뒤에 '피어' 차트와 표.
- cash_a(1~2): 잉여현금흐름, 순현금, 운전자본(매출채권·재고), 설비투자. 뒤에 '현금' 차트.
- cash_b(1~2): 주주환원(자기주식·배당), 발행주식수 변화, 진행 중인 자기주식 취득, 우선주 괴리율.
- market_a(1): 최근 주가 반응(발표일 등락, 지수 대비, 수급·시장 요인은 기사로). 뒤에 '1년 주가' 차트.
- market_b(1): 잠정실적 발표일 반응 이력. 뒤에 '발표 반응' 차트.
- market_c(0~1): 증권가·업계 시각과 변수(기사 인용).
- 자료의 "금융업"이 true면(은행·금융지주·보험) 현금흐름 절이 "주주환원" 절로 바뀐다: cash_head는 "주주환원: " 뒤에 붙고, cash_a는 빈 리스트, cash_b에 배당·자기주식만 쓴다. summary 4)도 주주환원으로 쓴다.
- 해당 차트·자료가 없는 그룹은 다른 사실로 채우고, 쓸 근거가 없으면 빈 리스트로 둔다(summary 제외).
- 블록 id는 "그룹.순번"(예: "earnings_a.1"). 블록 본문은 2~4문장, 본문 전체는 2,000~3,000자."""

GROUP_MAX = {"summary": 4, "earnings_a": 2, "earnings_b": 2, "valuation_a": 2, "valuation_b": 2, "cash_a": 2,
             "cash_b": 2, "market_a": 2, "market_b": 2, "market_c": 1}


def _check_draft(d: ReportDraft) -> str | None:
    if len(d.summary) != 4:
        return f"summary는 정확히 4개(받은 수 {len(d.summary)})"
    for g in ReportDraft.GROUPS:
        items = getattr(d, g)
        if len(items) > GROUP_MAX[g]:
            return f"{g}는 최대 {GROUP_MAX[g]}개"
        for i, b in enumerate(items, 1):
            if b.id != f"{g}.{i}":
                return f"블록 id는 '{g}.{i}' 형식(받은 값 {b.id})"
    return None


def evidence_prompt(data: ReportData, news: list[NewsItem], legal: list[dict] | None = None) -> str:
    facts = [{"id": k, "label": m.label, "display": display(m)} for k, m in data.facts.items()]
    charts = [{"chart": k, "title": v.get("title"), "subtitle": v.get("subtitle")} for k, v in data.charts.items()]
    tables = [v.get("title") for v in data.tables.values() if isinstance(v, dict) and v.get("title")]
    arts = [{"id": n.id, "outlet": next((x["outlet"] for x in data.news if x["id"] == n.id), None),
             "date": n.published_at.date().isoformat() if n.published_at else None, "title": n.title,
             "summary": n.analysis.summary if n.analysis else [], "quote": n.analysis.evidence if n.analysis else None}
            for n in news]
    disc = [{"date": d["date"], "title": d["title"], "gist": d.get("gist")} for d in data.disclosures]
    return json.dumps({"기업": data.corp["name"], "작성일": data.made, "최근 실적 기준": data.latest.get("label"),
                       "잠정실적": data.latest.get("prelim"),
                       "금융업": data.corp.get("industry") == "financial", "사실표": facts, "차트": charts, "표": tables,
                       "기사": arts, "최근 공시": disc, "법령 검토": legal or []}, ensure_ascii=False, indent=0)


def _system() -> list[dict]:
    return [{"type": "text", "text": WRITER_SYSTEM, "cache_control": {"type": "ephemeral"}}]


async def write_draft(data: ReportData, news: list[NewsItem], legal: list[dict] | None = None) -> ReportDraft:
    return await llm.structured(ReportDraft, [{"role": "user", "content": evidence_prompt(data, news, legal)}],
                                system=_system(), effort="medium", max_tokens=12_000, thinking=False,
                                task="company.write", validate=_check_draft)


# ---------------- 문체 검사(결정적)
FOLLOWS = {"earnings_a": "op", "earnings_b": "segments", "valuation_a": "band", "valuation_b": "peers",
           "cash_a": "cash", "market_a": "rel", "market_b": "events"}
_BANNED = re.compile(r"주목할 필요|지켜볼 필요|판단됨|시사함|의미함|것으로 보임|것으로 예상됨|본 리포트|제공된 자료|사실표")
_WRAP_END = re.compile(r"(구조|구간|셈|가능성)(임|이다|입니다)?$")
_SENT = re.compile(r"[^.!?]+[.!?]?")


def _norm(s: str) -> str:
    return re.sub(r"[\s.,·:()\[\]{}'\"‘’“”]|의|은|는|이|가|을|를|도", "", s)


def style_issues(draft: ReportDraft, data: ReportData) -> list:
    from difflib import SequenceMatcher

    from app.domain.models import VerificationIssue

    out = []
    for g, chart in FOLLOWS.items():
        title = (data.charts.get(chart) or {}).get("title")
        if not title:
            continue
        for b in getattr(draft, g):
            if SequenceMatcher(None, _norm(b.head), _norm(title)).ratio() >= 0.7:
                out.append(VerificationIssue(claim_id=b.id, type="style",
                                             detail=f"소제목이 바로 뒤 차트 제목('{title}')과 같은 말이다. 다른 각도의 주장으로 바꿔라"))
    for b in draft.blocks():
        body = re.sub(r"\[n\d+(?:\s*,\s*n\d+)*\]", "", b.text)
        bad = _BANNED.findall(b.head + " " + body)
        ends = [m.group(0).strip() for m in _SENT.finditer(body) if _WRAP_END.search(m.group(0).strip().rstrip(".!? "))]
        if bad or ends:
            out.append(VerificationIssue(claim_id=b.id, type="style",
                                         detail="쓰지 말아야 할 표현: " + ", ".join(sorted(set(bad)) + ends[:2])))
    return out


# ---------------- 재작성
class _HeadFix(BaseModel):
    field: str                   # "title", "earnings_head" 등
    text: str
    metric_ids: list[str]
    news_ids: list[str]


class _Rewrites(BaseModel):
    blocks: list[Block]
    heads: list[_HeadFix]


REVISE_NOTE = """

지금은 검증에 실패한 블록과 제목만 다시 쓴다. 블록은 같은 id, 제목은 같은 field를 유지한다.
- untraceable_number: 사실표에 있으면 슬롯으로 바꾸고, 기사 숫자면 기사 표기 그대로 쓰고 [n] id를 붙이고, 둘 다 아니면 지운다.
- missing_slot·missing_citation: 사실표에 있는 id, 기사 목록에 있는 id만 쓴다.
- partial·unsupported: 근거가 뒷받침하는 범위로 줄여 쓴다. 근거 없는 원인·전망은 지운다.
- investment_advice: 권유 표현을 지우고 사실만 남긴다.
- style: 지적한 표현을 고친다. 소제목이 차트 제목과 겹치면 차트가 보여 주지 않는 다른 사실(평균, 횟수, 원인, 비교 대상)을 앞세운 주장으로 바꾸고, 차트 제목의 핵심 낱말을 소제목에 쓰지 않는다."""


async def revise(draft: ReportDraft, ver: VerificationResult, data: ReportData, news: list[NewsItem],
                 legal: list[dict] | None = None) -> ReportDraft:
    failed = {i.claim_id for i in ver.issues}
    blocks = [b for b in draft.blocks() if b.id in failed]
    heads = [h for h in ReportDraft.HEADS if h in failed]
    feedback = {"블록": [{"block": b.model_dump(), "issues": [f"{i.type}: {i.detail}" for i in ver.issues
                                                           if i.claim_id == b.id]} for b in blocks],
                "제목": [{"field": h, "text": getattr(draft, h).text,
                        "issues": [f"{i.type}: {i.detail}" for i in ver.issues if i.claim_id == h]} for h in heads]}
    prompt = evidence_prompt(data, news, legal) + "\n\n다시 쓸 블록·제목과 사유:\n" + json.dumps(feedback, ensure_ascii=False, indent=0)

    def check(r: _Rewrites) -> str | None:
        got_b, got_h = sorted(b.id for b in r.blocks), sorted(h.field for h in r.heads)
        if got_b != sorted(b.id for b in blocks) or got_h != sorted(heads):
            return "실패한 블록 id와 제목 field를 모두 한 번씩 다시 써라"
        return None

    out = await llm.structured(_Rewrites, [{"role": "user", "content": prompt}],
                               system=[{"type": "text", "text": WRITER_SYSTEM + REVISE_NOTE}], effort="medium",
                               max_tokens=12_000, thinking=False, task="company.revise", validate=check)
    new_b = {b.id: b for b in out.blocks}
    upd: dict = {g: [new_b.get(b.id, b) for b in getattr(draft, g)] for g in ReportDraft.GROUPS}
    for h in out.heads:
        if h.field in ReportDraft.HEADS:
            upd[h.field] = Headline(text=h.text, metric_ids=h.metric_ids, news_ids=h.news_ids)
    return draft.model_copy(update=upd)


def finalize(draft: ReportDraft, ver: VerificationResult | None, fallback_title: str) -> tuple[ReportDraft, list[str]]:
    """재작성 뒤에도 실패한 블록은 지우고, 실패한 제목은 대체한다(리포트 제목 → 대표 차트 제목, 절 제목 → 비움)."""
    if ver is None or ver.passed:
        return draft, []
    # 문체 지적만 남은 블록은 내용이 맞으므로 남긴다
    failed = {i.claim_id for i in ver.issues if i.type != "style"}
    dropped = sorted(failed)
    upd: dict = {g: [b for b in getattr(draft, g) if b.id not in failed] for g in ReportDraft.GROUPS}
    for h in ReportDraft.HEADS:
        if h in failed:
            upd[h] = Headline(text=fallback_title if h == "title" else "")
    return draft.model_copy(update=upd), dropped


def legal_digest(findings) -> list[dict]:
    out = []
    for f in findings or []:
        if f.answer.answerable:
            out.append({"question": f.question, "points": [p.text for p in f.answer.points]})
    return out
