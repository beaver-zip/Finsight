"""legal_qa 초안(100문항): 단일 조문 30, 다중 홉 30, 별표 의존 15, 수치·기한 10, 답 없음 15.

- 근거 조문은 코드가 고른다(다중 홉은 인용 그래프의 코퍼스 내 엣지, 별표는 관련 조문이 있는 별표).
- Haiku 5.5가 그 조문만 보고 질문과 key_facts를 쓴다. 사람이 gold_keys·key_facts를 검수한다(source="llm_draft").
사용: cd backends && PYTHONPATH=. python -m eval.build.legal_qa   (비용 약 0.05달러)
"""
from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel

from app.agents.legal.tools import doc_text, short_name
from app.core import llm
from app.core.law.corpus import load_corpus
from app.domain.citations import build_edges
from eval.common import DATASETS, assign_splits, rng, save_jsonl

N = {"single": 30, "multi_hop": 30, "annex": 15, "numeric": 10, "unanswerable": 15}
NUM = re.compile(r"\d+\s*(?:일|개월|년|억원|만원|원|%|퍼센트|명|시간|영업일)")


class _Draft(BaseModel):
    question: str
    key_facts: list[str]


class _Unans(BaseModel):
    questions: list[str]


SYSTEM = """너는 전자금융 법령 평가셋을 만드는 법무 리서처다. 함께 준 조문만 보고, 실무자가 실제로 물을 법한 한국어 질문 하나와
그 질문의 정답에 반드시 들어가야 할 핵심 사실(key_facts) 2~3개를 쓴다.
- 질문에는 조문 번호나 법령명을 쓰지 않는다(검색으로 찾아야 한다). 약칭(전금법, CISO)은 써도 된다.
- key_facts는 "출처 조문: 내용" 형식의 한 문장이고, 조문 문구에서 확인되는 사실만 쓴다."""

PROMPTS = {
    "single": "아래 조문 하나로 답할 수 있는 질문을 써라.",
    "multi_hop": "아래 두 조문을 모두 읽어야 완전히 답할 수 있는 질문을 써라(한쪽이 다른 쪽을 참조·위임한다). key_facts에는 두 조문의 사실을 각각 넣는다.",
    "annex": "아래 조문과 별표를 모두 읽어야 답할 수 있는 질문을 써라. 별표의 구체적 기준(수치·자격·항목)을 묻는다.",
    "numeric": "아래 조문에 나오는 수치·기한·금액을 정확히 답해야 하는 질문을 써라. key_facts에 그 수치를 그대로 넣는다.",
}


async def draft(kind: str, docs: list) -> _Draft:
    body = "\n\n".join(f"[{short_name(d.key)}]\n{doc_text(d)[:3000]}" for d in docs)
    return await llm.structured(_Draft, [{"role": "user", "content": f"{PROMPTS[kind]}\n\n{body}"}], system=SYSTEM,
                                effort="low", task="eval.legal_qa",
                                validate=lambda d: None if 2 <= len(d.key_facts) <= 3 else "key_facts 2~3개")


async def main() -> None:
    c = load_corpus()
    r = rng()
    arts = [a for a in c.articles.values() if sum(len(p.text) for p in a.paragraphs) > 120 and a.no > 2]
    edges, _ = build_edges([(a.key.split(":")[0], a) for a in c.articles.values()], set(c.articles) | set(c.annexes))
    cross = [e for e in edges if e.dst in c.articles and e.src.split(":")[0] != e.dst.split(":")[0]]
    seen: set[tuple] = set()
    picks: list[tuple[str, list]] = []
    for e in r.sample(cross, len(cross)):
        pair = tuple(sorted((e.src, e.dst)))
        if pair in seen:
            continue
        seen.add(pair)
        picks.append(("multi_hop", [c.get(e.dst), c.get(e.src)]))
        if sum(1 for k, _ in picks if k == "multi_hop") == N["multi_hop"]:
            break
    # 별표와 그 별표를 근거로 하는 조문: 제목의 "(제N조 관련)" 또는 별표를 참조하는 조문(인용 그래프)
    rel = {x.key: x.related_article for x in c.annexes.values() if x.related_article in c.articles}
    for e in edges:
        if e.dst in c.annexes and e.dst not in rel and e.src in c.articles:
            rel[e.dst] = e.src
    for k in r.sample(sorted(rel), min(N["annex"], len(rel))):
        picks.append(("annex", [c.get(rel[k]), c.get(k)]))
    used = {d.key for _, ds in picks for d in ds}
    numeric = [a for a in arts if NUM.search(doc_text(a)) and a.key not in used]
    for a in r.sample(numeric, N["numeric"]):
        picks.append(("numeric", [a]))
        used.add(a.key)
    for a in r.sample([a for a in arts if a.key not in used], N["single"]):
        picks.append(("single", [a]))
    # 별표가 모자라면(관련 조문이 있는 별표 수) 단일 조문으로 채운다
    while len(picks) < sum(v for k, v in N.items() if k != "unanswerable"):
        a = r.choice([a for a in arts if a.key not in used])
        used.add(a.key)
        picks.append(("single", [a]))

    sem = asyncio.Semaphore(4)

    async def one(i, kind, docs):
        async with sem:
            d = await draft(kind, docs)
        return {"id": f"lq-{i:04d}", "question": d.question, "type": kind, "gold_keys": [x.key for x in docs],
                "key_facts": d.key_facts, "answerable": True, "as_of": None, "source": "llm_draft"}

    rows = await asyncio.gather(*(one(i, k, ds) for i, (k, ds) in enumerate(picks, 1)))
    titles = ", ".join(sorted({a.title for a in c.articles.values() if a.title})[:200])
    un = await llm.structured(_Unans, [{"role": "user", "content":
        f"전자금융거래법·시행령·전자금융감독규정·시행세칙의 조문 제목 목록:\n{titles}\n\n"
        f"이 법령들로는 답할 수 없지만 금융권 실무자가 이 챗봇에 물을 법한 질문 {N['unanswerable']}개를 써라. "
        "가상자산, 보험계약, 대부업 금리, 개인정보 파기 기간처럼 다른 법령 소관이거나 위 목록에 없는 주제로 한다."}],
        effort="low", task="eval.legal_qa", validate=lambda u: None if len(u.questions) >= N["unanswerable"] else "개수")
    for j, q in enumerate(un.questions[:N["unanswerable"]], len(rows) + 1):
        rows.append({"id": f"lq-{j:04d}", "question": q, "type": "unanswerable", "gold_keys": [], "key_facts": [],
                     "answerable": False, "as_of": None, "source": "llm_draft"})
    rows = assign_splits(rows, strata=lambda x: x["type"])
    save_jsonl(DATASETS / "legal_qa.v1.jsonl", rows)
    from collections import Counter
    print(len(rows), Counter(x["type"] for x in rows), Counter(x["split"] for x in rows))


if __name__ == "__main__":
    asyncio.run(main())
