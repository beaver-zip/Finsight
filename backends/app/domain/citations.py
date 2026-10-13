"""조문 본문의 참조 표현 → (법령, 조, 가지, 항, 호) 키. I/O 없는 순수 로직.

문서마다 접두어가 가리키는 법령이 다르다(설계서 '참조 해석기' 표):

| 쓰인 문서 | 법 | 영·시행령 | 규정 | 접두어 없음 |
| 전자금융거래법 | — | — | — | 자기 자신 |
| 시행령 | 전자금융거래법 | 자기 자신 | — | 자기 자신 |
| 감독규정 | 전자금융거래법 | 시행령 | 자기 자신 | 자기 자신 |
| 시행세칙 | 전자금융거래법 | 시행령 | 감독규정 | 자기 자신 |

- `같은 법`·`동법`은 바로 앞 참조의 법령, `같은 법 시행령`은 그 법령의 시행령, `같은 조`는 바로 앞 참조의 조문이다.
- 접두어가 없어도 바로 앞 참조와 `및`·`또는`·`,`·`ㆍ`로만 이어지면 앞 참조의 법령을 따른다("법 제9조 및 제10조").
- `제N조부터 제M조까지`는 그 사이 조문 각각(가지 조문은 알 수 없어 본조만)으로 푼다.
- 단독 `제N항`(같은 조문 안의 항)과 같은 조문을 가리키는 참조는 엣지로 쓰지 않으므로 돌려주지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

ACT = "전자금융거래법"
DECREE = "전자금융거래법시행령"
REG = "전자금융감독규정"
RULE = "전자금융감독규정시행세칙"
CORPUS = (ACT, DECREE, REG, RULE)

# 문서별 접두어 → 법령 key
ABBR: dict[str, dict[str, str]] = {
    ACT: {"이법": ACT, "법": ACT},
    DECREE: {"법": ACT, "이영": DECREE, "영": DECREE, "시행령": DECREE},
    REG: {"법": ACT, "시행령": DECREE, "영": DECREE, "이규정": REG, "규정": REG},
    RULE: {"법": ACT, "시행령": DECREE, "영": DECREE, "규정": REG, "이세칙": RULE, "세칙": RULE},
}
# 시행령을 가진 법률(같은 법 시행령)
DECREE_OF = {ACT: DECREE}

REF = re.compile(
    r"(?:「(?P<law>[^」]+)」\s*)?"
    r"(?:(?<![가-힣])(?P<abbr>같은\s*법\s*시행령|동법\s*시행령|이\s*법|같은\s*법|동법|이\s*영|같은\s*영|시행령|영"
    r"|이\s*규정|같은\s*규정|규정|이\s*세칙|세칙|같은\s*조|동조|법)\s*)?"
    r"제(?P<no>\d+)조(?:의(?P<branch>\d+))?"
    r"(?:\s*제(?P<para>\d+)항)?(?:\s*제(?P<item>\d+)호)?"
)
LAW_ONLY = re.compile(r"「(?P<law>[^」]+)」(?!\s*제\d+조)")
ANNEX = re.compile(r"(?:(?<![가-힣])(?P<abbr>시행령|영|규정|법)\s*)?[\[<＜]?\s*별표\s*(?P<no>\d+)(?:의(?P<branch>\d+))?")
_SEP = r"(?:및|또는|그리고|이나|나|와|과|,|ㆍ|·|부터|까지|내지)"
# 앞 참조와 이 참조 사이에 접속어와 항·호 표기만 있으면 같은 법령으로 본다("제11조제1항ㆍ제4항, 제13조의2")
_JOIN = re.compile(rf"^(?:\s*{_SEP}?\s*제\d+(?:항|호)(?:의\d+)?)*\s*{_SEP}?\s*{_SEP}?\s*$")
_FROM = re.compile(r"^\s*부터\s*$")
_UNTIL = re.compile(r"^\s*까지")
_THRU = re.compile(r"^\s*내지\s*$")
DELEGATION = {
    DECREE: re.compile(r"대통령령(?:으로|이)\s*정[하한]"),
    REG: re.compile(r"금융위원회(?:가|는|에서)?\s*(?:정[하한]|고시)"),
    RULE: re.compile(r"(?:금융감독원장|감독원장)(?:이|은)?\s*정[하한]"),
}


@dataclass(frozen=True)
class Ref:
    law: str                     # 대상 법령 key("전자금융거래법시행령", 코퍼스 밖이면 공백 뺀 법령명)
    no: int | None               # None이면 법령 자체(「…」만 쓴 경우)
    branch: int = 0
    para: int | None = None
    item: int | None = None
    raw: str = ""
    from_para: int | None = None
    annex: bool = False
    span: tuple[int, int] = (-1, -1)   # 본문 안 위치(범위로 풀린 사이 조문은 끝 참조의 위치)

    @property
    def in_corpus(self) -> bool:
        return self.law in CORPUS

    @property
    def key(self) -> str:
        if self.no is None:
            return self.law
        if self.annex:
            return f"{self.law}:annex-{self.no}" + (f"-{self.branch}" if self.branch else "")
        return f"{self.law}:{self.no}" + (f"-{self.branch}" if self.branch else "")


def law_key(name: str) -> str:
    """「전자금융거래법 시행령」 → 전자금융거래법시행령."""
    return re.sub(r"\s+", "", name)


def _abbr_law(doc: str, abbr: str, prev_law: str | None) -> str | None:
    a = re.sub(r"\s+", "", abbr)
    if a in ("같은법", "동법"):
        return prev_law
    if a in ("같은법시행령", "동법시행령"):
        return DECREE_OF.get(prev_law or "", (prev_law or "") + "시행령") if prev_law else None
    if a in ("같은영",):
        return prev_law if prev_law and prev_law.endswith("시행령") else DECREE
    if a in ("같은규정",):
        return prev_law or REG
    return ABBR.get(doc, {}).get(a)


def find_refs(doc: str, text: str, *, article: str | None = None, from_para: int | None = None) -> list[Ref]:
    """doc: 이 본문이 들어 있는 법령 key. article: 이 본문의 조문 key(자기 조문 참조를 거르는 데 쓴다)."""
    out: list[Ref] = []
    prev: Ref | None = None
    prev_end = -1
    laws_only = list(LAW_ONLY.finditer(text))
    for m in REF.finditer(text):
        law: str | None
        # 사이에 조문 없이 「법령」만 나온 경우: 그 법령을 바로 앞 참조로 삼는다("「은행법」에 따른 … 같은 법 제58조")
        lo = [x for x in laws_only if prev_end <= x.start() < m.start()]
        if lo:
            prev = Ref(law_key(lo[-1]["law"]), None, raw=lo[-1].group(0), from_para=from_para)
            prev_end = lo[-1].end()
        if m["law"]:
            law = law_key(m["law"])
        elif m["abbr"] and re.sub(r"\s+", "", m["abbr"]) in ("같은조", "동조"):
            if prev is None or prev.no is None:
                continue                       # 같은 조 = 지금 조문(엣지 아님)
            law = prev.law
            r = Ref(law, prev.no, prev.branch, _int(m["para"]) or None, _int(m["item"]), m.group(0).strip(), from_para,
                    span=m.span())
            out.append(r)
            prev, prev_end = r, m.end()
            continue
        elif m["abbr"]:
            law = _abbr_law(doc, m["abbr"], prev.law if prev else None)
        elif prev is not None and _JOIN.match(text[prev_end:m.start()]):
            law = prev.law
        else:
            law = doc
        if law is None:
            continue
        no, branch = int(m["no"]), _int(m["branch"]) or 0
        r = Ref(law, no, branch, _int(m["para"]), _int(m["item"]), m.group(0).strip(), from_para, span=m.span())
        # 범위: 앞 참조부터 이 참조까지 사이 조문
        gap = text[prev_end:m.start()]
        if prev is not None and prev.law == law and prev.no is not None and \
                ((_FROM.match(gap) and _UNTIL.match(text[m.end():])) or _THRU.match(gap)):
            for k in range(prev.no + 1, no):
                out.append(Ref(law, k, 0, None, None, f"{prev.raw}~{r.raw}", from_para, span=m.span()))
        out.append(r)
        prev, prev_end = r, m.end()
    for m in laws_only:
        out.append(Ref(law_key(m["law"]), None, raw=m.group(0), from_para=from_para, span=m.span()))
    for m in ANNEX.finditer(text):
        law = ABBR.get(doc, {}).get(m["abbr"], doc) if m["abbr"] else doc
        out.append(Ref(law, int(m["no"]), _int(m["branch"]) or 0, raw=m.group(0).strip(), from_para=from_para, span=m.span(),
                       annex=True))
    return [r for r in out if not (article and r.key == article)]


def _int(s: str | None) -> int | None:
    return int(s) if s else None


def delegation_phrase(lower_law: str, upper_text: str) -> str | None:
    """상위 조문 본문에서 하위 문서(lower_law)로 위임하는 문구. 없으면 None."""
    pat = DELEGATION.get(lower_law)
    m = pat.search(upper_text) if pat else None
    return m.group(0) if m else None


# ---------------- 그래프 엣지
LEVEL_RANK = {ACT: 0, DECREE: 1, REG: 2, RULE: 3}
IMPLEMENTS = [(DECREE, ACT), (REG, DECREE), (RULE, REG)]


@dataclass(frozen=True)
class Edge:
    src: str                     # 조문 key
    dst: str                     # 조문·별표 key 또는 법령 key(코퍼스 밖)
    kind: str                    # "REFERS_TO" | "DELEGATES_TO"
    raw: str = ""
    from_para: int | None = None
    to_para: int | None = None
    to_item: int | None = None
    phrase: str = ""
    source: str = ""             # DELEGATES_TO: "regex"(정규식만) | "api"(위임법령 조회만) | "both"(둘 다)


def build_edges(articles: list, known: set[str], api: list[dict] | None = None) -> tuple[list[Edge], set[str]]:
    """articles: (법령 key, Article) 목록. known: 코퍼스의 조문·별표 key. api: 위임법령 조회 결과(parser.parse_delegations).
    돌려주는 것: 엣지 목록, 참조됐지만 코퍼스 밖인 법령 key 집합.

    - REFERS_TO: 조문 → 참조한 조문·별표(같은 (출발, 도착, 출발 항)은 하나로), 코퍼스 밖 법령은 법령 노드로.
    - DELEGATES_TO: 두 가지로 찾아 맞춰 본다.
      정규식: 하위 문서 조문 B가 상위 문서 조문 A를 참조하고, A 본문에 하위 문서로 위임하는 문구가 있으면 A → B.
      API: 위임법령 조회가 준 (위임한 조문 → 시행령 조문) 쌍. 행정규칙은 API가 문서만 알려 줘서 조문 쌍은 정규식으로만 찾고,
      같은 상위 조문이 그 행정규칙에 위임한다고 API가 확인하면 "both"로 둔다.
      API 정보가 있는 상위 문서(법률·시행령)에서 정규식만 찾은 쌍은 위임이 아니라고 보고 뺀다(참조 엣지는 남는다).
      API가 없는 상위 문서(감독규정 → 시행세칙)는 정규식 결과를 그대로 쓴다.
    """
    texts = {a.key: "\n".join(p.text for p in a.paragraphs) for _, a in articles}
    edges: dict[tuple, Edge] = {}
    outside: set[str] = set()
    for law, a in articles:
        for p in a.paragraphs:
            for r in find_refs(law, p.text, article=a.key, from_para=p.no):
                if not r.in_corpus:
                    outside.add(r.law)
                    edges.setdefault((a.key, r.law, "REFERS_TO", None),
                                     Edge(a.key, r.law, "REFERS_TO", r.raw))
                    continue
                if r.no is None or r.key not in known:
                    continue
                edges.setdefault((a.key, r.key, "REFERS_TO", p.no),
                                 Edge(a.key, r.key, "REFERS_TO", r.raw, p.no, r.para, r.item))
                if not r.annex and LEVEL_RANK.get(r.law, 9) < LEVEL_RANK.get(law, 9):
                    phrase = delegation_phrase(law, texts.get(r.key, ""))
                    if phrase:
                        edges.setdefault((r.key, a.key, "DELEGATES_TO", None),
                                         Edge(r.key, a.key, "DELEGATES_TO", r.raw, phrase=phrase, source="regex"))
    doc_level = set()                      # API가 확인한 (상위 조문, 위임받은 행정규칙 문서)
    for d in api or []:
        if d.get("dst") is None:
            doc_level.add((d["src"], d["dst_law"]))
            continue
        if d["src"] not in known or d["dst"] not in known:
            continue
        k = (d["src"], d["dst"], "DELEGATES_TO", None)
        if k in edges:
            edges[k] = replace(edges[k], source="both")
        else:
            edges[k] = Edge(d["src"], d["dst"], "DELEGATES_TO", d.get("clause", ""), phrase=d.get("phrase", ""),
                            source="api")
    covered = {d["src"].split(":")[0] for d in api or []}   # API 위임 정보가 있는 상위 문서
    for k, e in list(edges.items()):
        if e.kind != "DELEGATES_TO" or e.source != "regex":
            continue
        if (e.src, e.dst.split(":")[0]) in doc_level:
            edges[k] = replace(e, source="both")
        elif e.src.split(":")[0] in covered:
            # API가 이 상위 문서의 위임을 모두 알려 주는데 없는 쌍: 정의 조항을 참조만 한 경우 등. 참조 엣지만 남긴다
            del edges[k]
    return list(edges.values()), outside
