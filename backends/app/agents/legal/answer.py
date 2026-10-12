"""법령 답변: 조문을 search_result 블록으로 넘겨 cited()로 받고, 인용을 조문 key·항으로 바꾼다. 마크다운 렌더링도 여기서.

- 조문 하나 = search_result 하나(항마다 content 블록 하나), 별표는 1,000자 단위 블록. key 순으로 넘긴다.
- 응답 text 블록들을 빈 줄(\\n\\n) 기준으로 묶어 LegalPoint 하나로 만든다. 첫 점은 핵심 요약이다.
- 인용이 넘긴 문서만 가리키는 것은 API가 보장한다.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote

import logging

from app.agents.legal.tools import LawTools, doc_text, short_name
from app.core import llm
from app.domain.models import Annex, Article, ArticleHit, CitedSpan, LegalAnswer, LegalPoint

SYSTEM = """너는 전자금융 법령을 조사해 답하는 법무 리서처다. 함께 준 조문만 근거로 한국어로 답한다.

- 첫 문단은 질문에 대한 결론 1~2문장이다.
- 이어서 쟁점마다 한 문단씩 쓰고, 문단마다 근거 조문을 인용한다. 문단 사이는 빈 줄로 나눈다.
- 조문에 없는 내용, 조문 밖의 일반론, 실무 조언을 쓰지 않는다. 조문으로 답할 수 없는 쟁점은 "제공된 조문에서 확인되지 않는다"고 쓴다.
- 법령명은 정식 명칭으로 쓴다(예: 전자금융거래법 시행령 제11조의4제4항).
- 조문 문구를 길게 그대로 옮겨 적지 말고 요지를 너의 문장으로 쓴다.
- "~구조입니다", "~구간", "~셈", "~가능성이 있습니다" 같은 해석용 맺음말과 "시사합니다", "의미합니다"를 쓰지 않는다. 조문이 정한 사실만 쓴다.
- 존댓말(~습니다)로 쓴다. 머리말, 소제목, 목록 기호, 맺음말을 쓰지 않는다."""

log = logging.getLogger(__name__)
LAW_URL = "https://www.law.go.kr/법령/{name}/{art}"
ADMRUL_URL = "https://www.law.go.kr/행정규칙/{name}"


def _blocks(doc: Article | Annex) -> list[str]:
    if isinstance(doc, Annex):
        out, buf = [], ""
        for line in doc.text.split("\n"):
            if buf and len(buf) + len(line) + 1 > 1000:
                out.append(buf)
                buf = ""
            buf = f"{buf}\n{line}" if buf else line
        return out + ([buf] if buf else [])
    return [p.text for p in doc.paragraphs]


def _title(doc: Article | Annex) -> str:
    if isinstance(doc, Annex):
        no = doc.key.split(":annex-")[-1].replace("-", "의")
        return f"{doc.law_name} [별표 {no}] {doc.title}"
    return doc.display_title


def search_results(docs: list[Article | Annex]) -> list[dict]:
    return [{"type": "search_result", "source": d.key, "title": _title(d),
             "content": [{"type": "text", "text": t} for t in _blocks(d)], "citations": {"enabled": True}}
            for d in sorted(docs, key=lambda d: d.key)]


def _norm(t: str) -> str:
    return re.sub(r"[\s\"'“”‘’「」.,·ㆍ()①-⑳]", "", t)


def _is_quote(text: str, cites: list[CitedSpan]) -> bool:
    """이 text 블록이 인용한 조문의 원문을 거의 그대로 옮긴 것인가."""
    from difflib import SequenceMatcher

    t = _norm(text)
    if len(t) < 15:
        return False
    src = _norm(" ".join(c.cited_text for c in cites))
    if t in src:
        return True
    m = SequenceMatcher(None, t, src, autojunk=False)
    return sum(b.size for b in m.get_matching_blocks()) / len(t) >= 0.8


def points_from(resp, docs: list[Article | Annex]) -> list[LegalPoint]:
    by_key = {d.key: d for d in docs}
    points: list[LegalPoint] = []
    cur_text, cur_cites = "", []

    def flush():
        nonlocal cur_text, cur_cites
        t = re.sub(r"\s*\(\s*\)", "", cur_text)          # 원문 조각을 뺀 자리의 빈 괄호
        t = re.sub(r"\s+([.,])", r"\1", t)
        t = re.sub(r"^#+\s*", "", t.strip())
        if t:
            points.append(LegalPoint(text=t, citations=cur_cites))
        cur_text, cur_cites = "", []

    for b in resp.content:
        if getattr(b, "type", None) != "text":
            continue
        parts = b.text.split("\n\n")
        cites = []
        for c in getattr(b, "citations", None) or []:
            if getattr(c, "type", "") != "search_result_location":
                continue
            d = by_key.get(c.source)
            para = None
            if isinstance(d, Article) and 0 <= c.start_block_index < len(d.paragraphs):
                para = d.paragraphs[c.start_block_index].no
            cites.append(CitedSpan(source=c.source, paragraph_index=para, cited_text=c.cited_text))
        if cites and _is_quote(b.text, cites):
            # 조문 원문을 그대로 옮긴 조각. 문장 뒤에 덧붙었거나 괄호 안이면 빼고 인용만 남기고,
            # 문장 중간에 목적어로 들어갔으면("제2항은 [원문]고 규정한다") 따옴표로 감싸 남긴다
            prev = cur_text.rstrip()
            if not prev or prev.endswith(("(", ".", "다", "요", ":")):
                parts = [""]
            else:
                parts = [f"“{b.text.strip()}”"]
        cur_text += parts[0]
        for extra in parts[1:]:
            flush()
            cur_text = extra
        cur_cites += cites                   # 인용은 블록의 마지막 조각(인용한 주장)에 붙인다
    flush()
    return points


async def build_answer(question: str, context: list[ArticleHit], tools: LawTools, *,
                       plan=None) -> tuple[LegalAnswer, dict[str, str]]:
    docs = [h.article for h in context]
    resp = await llm.cited(question, search_results(docs), system=SYSTEM, effort="medium", task="legal.answer")
    points = points_from(resp, docs)
    if not any(p.citations for p in points):     # 인용 없이 답하는 경우가 가끔 있다(2026-10-12 관찰): 한 번 더 요청
        log.warning("법령 답변에 인용이 없어 다시 요청한다")
        resp = await llm.cited(question + "\n\n(각 문단의 근거 조문을 반드시 인용하라.)", search_results(docs),
                               system=SYSTEM, effort="medium", task="legal.answer")
        points = points_from(resp, docs)
    cited = {c.source for p in points for c in p.citations}
    paths = [h.path for h in context if h.article.key in cited and len(h.path) > 1]
    notes = []
    as_of: date | None = getattr(plan, "as_of", None)
    effs = [d.effective_date for d in docs if isinstance(d, Article) and d.key in cited and d.effective_date]
    if as_of and effs and as_of < max(effs):
        notes.append(f"현행 조문(시행일 {max(effs).isoformat()}) 기준")
    try:
        outside = await tools.graph.outside_laws(sorted(k for k in cited if ":annex-" not in k))
    except Exception:  # noqa: BLE001
        outside = []
    if outside:
        notes.append("참조했지만 코퍼스에 없는 법령: " + ", ".join(outside))
    texts = {d.key: doc_text(d) for d in docs}
    return LegalAnswer(answerable=True, points=points, paths=paths, notes=notes), texts


# ---------------- 렌더링
def link_of(key: str, corpus) -> str:
    """law.go.kr 조문 주소. 사용자가 올린 내규는 공개 주소가 없어 빈 문자열."""
    d0 = corpus.get(key)
    if d0 is not None and getattr(d0, "source", "lawgo") == "upload":
        return ""
    law = key.split(":")[0]
    d = next((x for x in corpus.docs if x["key_name"] == law), None)
    name = d["name"] if d else law
    if d and d["level"] in ("법률", "시행령") and ":annex-" not in key:
        art = short_name(key).split(" ", 1)[1]
        return LAW_URL.format(name=quote(name), art=quote(art))
    return ADMRUL_URL.format(name=quote(name)) if d and d["level"] in ("고시", "세칙") else \
        f"https://www.law.go.kr/법령/{quote(name)}"


def cite_label(c: CitedSpan) -> str:
    return short_name(c.source) + (f"제{c.paragraph_index}항" if c.paragraph_index else "")


def render_markdown(ans: LegalAnswer, corpus) -> str:
    """핵심 요약 · 상세 내용 · 참고 문헌."""
    if not ans.answerable:
        lines = ["제공된 법령(전자금융거래법, 같은 법 시행령, 전자금융감독규정, 같은 규정 시행세칙)에서 "
                 "이 질문에 답할 조문을 찾지 못했습니다. 관련도가 가장 높은 조문은 아래와 같습니다.", ""]
        for k in ans.fallback:
            d = corpus.get(k)
            url = link_of(k, corpus)
            lines.append(f"- [{_title(d) if d else k}]({url})" if url else f"- {_title(d) if d else k}")
        return "\n".join(lines)
    refs: list[str] = []
    for p in ans.points:
        for c in p.citations:
            lab = cite_label(c)
            if lab not in refs:
                refs.append(lab)

    def marks(p: LegalPoint) -> str:
        ns = sorted({refs.index(cite_label(c)) + 1 for c in p.citations})
        return "".join(f"[{n}]" for n in ns)

    out = ["### 핵심 요약", ans.points[0].text + marks(ans.points[0]) if ans.points else "", ""]
    if len(ans.points) > 1:
        out.append("### 상세 내용")
        out += [f"{p.text}{marks(p)}\n" for p in ans.points[1:]]
    if ans.paths:
        out.append("조문 연결: " + " / ".join(" → ".join(short_name(k) for k in path) for path in ans.paths))
        out.append("")
    out.append("### 참고 문헌")
    for i, lab in enumerate(refs, 1):
        src = next(c.source for p in ans.points for c in p.citations if cite_label(c) == lab)
        d = corpus.get(src)
        title = f" {d.title}" if d is not None and getattr(d, "title", "") else ""
        url = link_of(src, corpus)
        out.append(f"{i}. [{lab}{title}]({url})" if url else f"{i}. {lab}{title}(내규)")
    if ans.notes:
        out += [""] + [f"※ {n}" for n in ans.notes]
    return "\n".join(out).strip()
