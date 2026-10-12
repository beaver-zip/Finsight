"""news_labels 초안: 8개 기업 × 후보(최대 약 60건, N2 실행 후보 = N1 후보 ∪ 커버리지 재검색 후보).

- 후보 풀은 N2 구성(커버리지 재검색 켬)으로 파이프라인을 한 번 돌려 얻는다. 처음 검색분이 N1 후보, 재검색분이 N2 추가분이다.
- 라벨 초안은 시스템의 기사 분류와 독립된 프롬프트로 Haiku 5.5가 쓴다(관련도 0·1·2, 측면, 중요도 1~3, 중복 묶음, 발행일).
  사람이 검수한다. 기사 원문은 저장소에 넣지 않는다(제목·URL·스니펫·날짜·라벨만).
사용: cd backends && PYTHONPATH=. python -m eval.build.news_labels   (Tavily 약 40크레딧, 약 0.1달러)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

import httpx
from pydantic import BaseModel

from app.agents.news.pipeline import NewsConfig, run_pipeline
from app.core import llm
from app.core.dart import corp as dart_corp
from app.core.dart import filings as dart_filings
from app.core.dart.client import DartClient
from app.domain.models import ASPECTS
from eval.common import DATASETS, assign_splits, load_jsonl, save_jsonl

log = logging.getLogger("news_labels")


class _Label(BaseModel):
    cid: str
    relevance: int          # 0 다른 기업이거나 이름만 나옴, 1 다루지만 주제가 아님, 2 기사의 주체
    aspect: str | None      # 6개 측면 중 하나, 관련도 0이면 null
    materiality: int        # 1 일상 보도, 2 실적·사업에 영향 가능, 3 실적·주가에 직접 영향
    dup_group: str          # 같은 사건을 다룬 기사끼리 같은 문자열
    published: str | None   # 본문·스니펫에서 확인한 발행일 YYYY-MM-DD, 모르면 null


class _Labels(BaseModel):
    labels: list[_Label]


RUBRIC = f"""너는 기업 뉴스 평가셋의 라벨러다. 기업 '{{corp}}'에 대한 기사 후보 목록을 보고 기사마다 라벨을 붙인다.
- relevance: 2 = 그 기업이 기사의 주체, 1 = 그 기업을 다루지만 주제가 아님, 0 = 다른 기업이거나 이름만 스친다.
- aspect: {', '.join(ASPECTS)} 중 하나. relevance 0이면 null.
- materiality: 1 = 일상 보도·행사, 2 = 실적·사업에 영향을 줄 수 있음, 3 = 실적·주가에 직접 영향.
- dup_group: 같은 사건(같은 발표·같은 공시)을 다룬 기사끼리 같은 짧은 이름(예: "3Q26잠정실적"). 다른 사건이면 다른 이름.
- published: 스니펫·본문 앞부분에서 확인되는 발행일(YYYY-MM-DD). 없으면 null.
모든 후보에 cid를 그대로 써서 하나씩 라벨을 낸다."""


async def label(corp_name: str, cands) -> list[_Label]:
    out: list[_Label] = []
    for i in range(0, len(cands), 20):
        part = cands[i:i + 20]
        body = "\n\n".join(f"[{c.id}] {c.title}\n날짜(수집): {c.published_at.date() if c.published_at else '없음'}\n"
                           f"{(c.text or '')[:600]}" for c in part)
        res = await llm.structured(_Labels, [{"role": "user", "content": body}], system=RUBRIC.format(corp=corp_name),
                                   effort="low", task="eval.news_labels",
                                   validate=lambda r, ids={c.id for c in part}: None if {x.cid for x in r.labels} >= ids
                                   else "모든 cid에 라벨")
        out += [x for x in res.labels if x.cid in {c.id for c in part}]
    return out


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for n in ("httpx", "httpcore"):
        logging.getLogger(n).setLevel(logging.WARNING)
    companies = load_jsonl(DATASETS / "news_companies.v1.jsonl")
    # 뉴스 평가의 dev/test는 기업 단위로 다시 나눈다(8개 중 약 2개가 dev)
    split = {c["corp_code"]: c["split"] for c in assign_splits([dict(c) for c in companies], strata=lambda x: "all")}
    path = DATASETS / "news_labels.v1.jsonl"
    rows = load_jsonl(path) if path.exists() else []          # 실행을 거듭하며 후보를 URL 기준으로 합친다(pooling)
    seen = {(r["corp_code"], r["url"]) for r in rows}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        dart = DartClient(http, concurrency=1)
        for co in companies:
            corp = await dart_corp.get_profile(dart, co["corp_code"])
            disc = await dart_filings.disclosures(dart, co["corp_code"])
            res = await run_pipeline(corp, disc, http=http, config=NewsConfig(coverage_retry=True))
            n1 = {c.url for c in res.candidates[:res.n_initial]}
            picked = {n.url for n in res.items}
            new = [c for c in res.candidates if (co["corp_code"], c.url) not in seen]
            labs = {x.cid: x for x in await label(corp.corp_name, new)} if new else {}
            for c in new:
                seen.add((co["corp_code"], c.url))
                lb = labs.get(c.id)
                rows.append({"id": f"{co['corp_code']}:{hashlib.sha1(c.url.encode()).hexdigest()[:10]}", "corp_code": co["corp_code"], "company": co["name"],
                             "url": c.url, "title": c.title, "snippet": (c.snippet or "")[:300],
                             "published_at": c.published_at.isoformat() if c.published_at else None,
                             "pool": "N1" if c.url in n1 else "N2",
                             "system_picked": c.url in picked,
                             "relevance": lb.relevance if lb else None, "aspect": lb.aspect if lb else None,
                             "materiality": lb.materiality if lb else None,
                             "dup_group": f"{co['corp_code']}:{lb.dup_group}" if lb else None,
                             "published_label": lb.published if lb else None,
                             "split": split[co["corp_code"]], "source": "llm_draft"})
            log.info("%s: 후보 %d(N1 %d, 새 후보 %d), 선택 %d", co["name"], len(res.candidates), len(n1), len(new),
                     len(res.items))
    save_jsonl(path, rows)
    log.info("라벨 %d건", len(rows))


if __name__ == "__main__":
    asyncio.run(main())
