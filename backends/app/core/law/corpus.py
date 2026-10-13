"""법령 코퍼스: 적재 대상 4개 문서의 수집과 저장본(data/law/corpus.json) 읽기.

저장본은 조문·별표 조회(get_article, get_annex)에 쓴다. Weaviate·Neo4j 없이도 조문 본문을 꺼낼 수 있다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from lxml import etree

from app.core.law.client import LawClient
from app.core.law.parser import LawDoc, parse, parse_delegations
from app.domain.models import Annex, Article
from app.settings import BACKEND_DIR

log = logging.getLogger(__name__)
CORPUS_FILE = BACKEND_DIR / "data" / "law" / "corpus.json"

# (검색 target, 법령명). 법령은 법령 검색, 고시·세칙은 행정규칙 검색으로 현행본 일련번호를 찾는다.
SOURCES = [("law", "전자금융거래법"), ("law", "전자금융거래법 시행령"),
           ("admrul", "전자금융감독규정"), ("admrul", "전자금융감독규정시행세칙")]


async def current_serial(client: LawClient, target: str, name: str) -> str:
    xml = await client.search(target, name, ttl=86400)
    root = etree.fromstring(xml.encode())
    tag, nm, cur, serial = (("law", "법령명한글", "현행연혁코드", "법령일련번호") if target == "law"
                            else ("admrul", "행정규칙명", "현행연혁구분", "행정규칙일련번호"))
    for el in root.findall(tag):
        if (el.findtext(nm) or "").strip() == name and (el.findtext(cur) or "").strip() == "현행":
            return (el.findtext(serial) or "").strip()
    raise LookupError(f"현행 {name}을 찾지 못했다")


async def fetch_all(client: LawClient) -> list[LawDoc]:
    docs = []
    for target, name in SOURCES:
        serial = await current_serial(client, target, name)
        xml = await client.service(target, **({"MST": serial} if target == "law" else {"ID": serial}))
        d = parse(xml)
        if target == "law":            # 위임법령 조회: 이 법령 조문이 시행령 조문·행정규칙에 위임한 쌍
            d.delegations = parse_delegations(await client.service("lsDelegated", MST=serial), d.key_name)
        log.info("%s: 일련번호 %s, 시행 %s, 조문 %d, 별표 %d, 위임 %d", d.name, serial, d.effective_date, len(d.articles),
                 len(d.annexes), len(d.delegations))
        docs.append(d)
    return docs


def save(docs: list[LawDoc]) -> None:
    CORPUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = [{"name": d.name, "key_name": d.key_name, "law_id": d.law_id, "level": d.level, "serial": d.serial,
             "effective_date": d.effective_date.isoformat() if d.effective_date else None,
             "promulgation_no": d.promulgation_no,
             "articles": [a.model_dump(mode="json") for a in d.articles],
             "annexes": [x.model_dump(mode="json") for x in d.annexes],
             "delegations": d.delegations} for d in docs]
    CORPUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    load_corpus.cache_clear()


@dataclass
class Corpus:
    docs: list[dict]
    delegations: list[dict]
    articles: dict[str, Article]
    annexes: dict[str, Annex]

    def get(self, key: str) -> Article | Annex | None:
        """코퍼스 조문·별표, 없으면 사용자가 올린 내규 조문."""
        from app.core.law import uploads

        return self.articles.get(key) or self.annexes.get(key) or uploads.get_article(key)

    def level_of(self, law_key: str) -> str | None:
        return next((d["level"] for d in self.docs if d["key_name"] == law_key), None)


@lru_cache
def load_corpus() -> Corpus:
    data = json.loads(CORPUS_FILE.read_text())
    arts = {a["key"]: Article.model_validate(a) for d in data for a in d["articles"]}
    anx = {x["key"]: Annex.model_validate(x) for d in data for x in d["annexes"]}
    return Corpus(docs=[{k: v for k, v in d.items() if k not in ("articles", "annexes", "delegations")} for d in data],
                  delegations=[x for d in data for x in d.get("delegations", [])],
                  articles=arts, annexes=anx)
