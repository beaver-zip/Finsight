"""법령 XML 파서와 참조 해석기. 픽스처는 2026-10-12에 법령 Open API에서 받은 현행본(OC 값은 지움)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.law.parser import clean, parse, split_paragraphs
from app.domain.citations import ACT, DECREE, REG, RULE, delegates, find_refs

FIX = Path(__file__).parent / "fixtures" / "lawgo"


@pytest.fixture(scope="module")
def docs():
    return {f: parse((FIX / f"{f}.xml").read_text()) for f in ("law_act", "law_decree", "admrul_reg", "admrul_rule")}


def _art(doc, key):
    return next(a for a in doc.articles if a.key == key)


def test_fixtures_have_no_oc_value():
    for p in FIX.glob("*.xml"):
        t = p.read_text()
        assert "OC=" not in t.replace("OC=***", "")


def test_parse_law_structure(docs):
    act = docs["law_act"]
    assert (act.key_name, act.level, len(act.articles)) == (ACT, "법률", 71)
    a = _art(act, "전자금융거래법:21-2")
    assert a.title == "정보보호최고책임자 지정" and a.chapter.startswith("제3장")
    assert [p.no for p in a.paragraphs] == [1, 2, 3, 4, 5]
    p4 = a.paragraphs[3].text
    assert p4.startswith("④") and "\n  5. 그 밖에" in p4 and "<개정" not in p4


def test_parse_decree_annex_and_related(docs):
    d = docs["law_decree"]
    assert d.key_name == DECREE and d.level == "시행령"
    an = next(x for x in d.annexes if x.key == "전자금융거래법시행령:annex-1")
    assert an.related_article == "전자금융거래법시행령:11-4" and "정보보호최고책임자의 자격" in an.text


def test_parse_admrul_splits_paragraphs(docs):
    reg = docs["admrul_reg"]
    assert (reg.key_name, reg.level) == (REG, "고시")
    a = _art(reg, "전자금융감독규정:8")
    assert [p.no for p in a.paragraphs] == [1, 2] and "\n  6. 최고경영자는" in a.paragraphs[0].text
    assert docs["admrul_rule"].level == "세칙"
    assert not any(p.text.startswith("삭제") for d in docs.values() for a in d.articles for p in a.paragraphs)


def test_clean_and_split():
    assert clean("① 지정하여야 한다. <개정 2013.5.22>\n  [본조 신설 2025. 2. .5.]") == "① 지정하여야 한다."
    assert clean("1의2. 삭제<2014.12.30>") == "1의2. 삭제"
    ps = split_paragraphs("X:1", "이 규정은 …\n  ① 첫째\n  1. 호\n  ② 둘째")
    assert [(p.key, p.no) for p in ps] == [("X:1:p0", 0), ("X:1:p1", 1), ("X:1:p2", 2)]


@pytest.mark.parametrize("doc,text,want", [
    (DECREE, "법 제21조의2제2항에서 \"대통령령으로 정하는", ["전자금융거래법:21-2"]),
    (DECREE, "제11조의4제1항에 따른", ["전자금융거래법시행령:11-4"]),
    (REG, "시행령 제11조의4제4항 및 법 제21조의2", ["전자금융거래법시행령:11-4", "전자금융거래법:21-2"]),
    (RULE, "규정 제15조제1항제3호나목", ["전자금융감독규정:15"]),
    (DECREE, "법 제9조 및 제10조", ["전자금융거래법:9", "전자금융거래법:10"]),
    (DECREE, "법 제39조제1항부터 제5항까지, 제40조에 따른", ["전자금융거래법:39", "전자금융거래법:40"]),
    (ACT, "제14조부터 제16조까지의 규정", ["전자금융거래법:14", "전자금융거래법:15", "전자금융거래법:16"]),
    (ACT, "「형법」 제214조 내지 제217조", ["형법:214", "형법:215", "형법:216", "형법:217"]),
    (REG, "「은행법」에 따른 지방금융회사 및 같은 법 제58조", ["은행법:58", "은행법"]),
    (REG, "「전자금융거래법 시행령」 제2조제1호", ["전자금융거래법시행령:2"]),
    (REG, "시행령 <별표 1>", ["전자금융거래법시행령:annex-1"]),
    (DECREE, "별표 1의2와 같다", ["전자금융거래법시행령:annex-1-2"]),
    (ACT, "방법으로 제3조에", ["전자금융거래법:3"]),          # '방법'의 '법'은 접두어가 아니다
])
def test_find_refs(doc, text, want):
    assert [r.key for r in find_refs(doc, text)] == want


def test_find_refs_skips_self_and_keeps_para():
    rs = find_refs(DECREE, "제11조의4제2항 및 같은 조 제3항, 법 제21조의2제4항제5호", article="전자금융거래법시행령:11-4")
    assert [(r.key, r.para, r.item) for r in rs] == [("전자금융거래법:21-2", 4, 5)]


def test_corpus_refs_all_resolve(docs):
    keys = {a.key for d in docs.values() for a in d.articles} | {x.key for d in docs.values() for x in d.annexes}
    refs = [r for d in docs.values() for a in d.articles for p in a.paragraphs
            for r in find_refs(d.key_name, p.text, article=a.key) if r.in_corpus and r.no is not None]
    assert len(refs) > 600 and all(r.key in keys for r in refs)


def test_delegation_phrases():
    assert delegates(ACT, DECREE, "자격요건 등에 필요한 사항은 대통령령으로 정한다")
    assert delegates(DECREE, REG, "그 밖에 필요한 사항은 금융위원회가 정하여 고시한다")
    assert delegates(REG, RULE, "세부기준은 감독원장이 정한다")
    assert not delegates(ACT, DECREE, "금융위원회가 정하는")
