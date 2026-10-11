"""법령 Open API XML → Article·Annex.

- 법령(law): 조문단위 → 항 → 호 → 목 구조. 조문여부가 '전문'인 단위는 장·절 머리다.
- 행정규칙(admrul): 조문내용 하나가 조문 하나(문자열)라서 항 머리(①…)로 나눈다. '제N장'만 있는 줄은 장 머리다.
- 별표: 별표내용(고정폭 텍스트)을 줄 끝 공백과 빈 줄을 정리해 그대로 쓴다.
- 본문의 개정 이력 표시(<개정 2013.5.22>, [본조신설 …])는 검색·인용에 쓸모가 없어 지운다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from lxml import etree

from app.domain.models import Annex, Article, Paragraph

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
# 연도가 든 꺾쇠·대괄호 표시: <개정 2013.5.22>, 삭제<2014.12.30>, [본조 신설 2025. 2. 5.], <종전의 제5호에서 이동, 2025. 2. 5.>
_HIST = re.compile(r"\s*[<＜][^<>＜＞\n]*\d{4}[^<>＜＞\n]*[>＞]+")
_HIST_BR = re.compile(r"\s*\[(?:[^\[\]\n]*\d{4}|종전)[^\[\]\n]*\]")
_HEAD = re.compile(r"^\s*제(?P<no>\d+)조(?:의(?P<branch>\d+))?\s*(?:\((?P<title>[^)]*)\))?\s*")
_CHAPTER = re.compile(r"^\s*제\d+장(?:의\d+)?\s+\S.*$")
_SECTION = re.compile(r"^\s*제\d+절(?:의\d+)?\s+\S.*$")
_PARA_SPLIT = re.compile(rf"(?:^|\n)\s*(?=[{CIRCLED}])")
_RELATED = re.compile(r"\(\s*제(?P<no>\d+)조(?:의(?P<branch>\d+))?[^)]*관련\s*\)")


@dataclass
class LawDoc:
    name: str                       # "전자금융거래법 시행령"
    key_name: str                   # "전자금융거래법시행령"(공백 없음, 조문 key 접두어)
    law_id: str
    level: str                      # 법률·시행령·고시·세칙
    serial: str                     # 법령일련번호(MST) 또는 행정규칙일련번호
    effective_date: date | None
    promulgation_no: str | None
    articles: list[Article] = field(default_factory=list)
    annexes: list[Annex] = field(default_factory=list)


def key_name(name: str) -> str:
    return re.sub(r"\s+", "", name)


def article_key(law_key: str, no: int, branch: int = 0) -> str:
    return f"{law_key}:{no}" + (f"-{branch}" if branch else "")


def annex_key(law_key: str, no: int, branch: int = 0) -> str:
    return f"{law_key}:annex-{no}" + (f"-{branch}" if branch else "")


def clean(text: str | None) -> str:
    t = _HIST_BR.sub("", _HIST.sub("", text or ""))
    lines = [ln.rstrip() for ln in t.replace("\r", "").split("\n")]
    return "\n".join(ln for ln in lines if ln.strip()).strip()


def _date(s: str | None) -> date | None:
    s = (s or "").strip()
    return date(int(s[:4]), int(s[4:6]), int(s[6:8])) if len(s) == 8 and s.isdigit() else None


def _level(kind: str) -> str:
    return {"법률": "법률", "대통령령": "시행령", "고시": "고시", "세칙": "세칙"}.get(kind, kind)


def _is_deleted(body: str) -> bool:
    return re.fullmatch(r"\s*삭제\s*", body or "") is not None


# ---------------- 법령(law)
def _para_text(p: etree._Element) -> str:
    parts = [p.findtext("항내용") or ""]
    for ho in p.findall("호"):
        parts.append("  " + (ho.findtext("호내용") or "").strip())
        for mok in ho.findall("목"):
            parts.append("    " + (mok.findtext("목내용") or "").strip())
    return clean("\n".join(parts))


def parse_law(xml: str) -> LawDoc:
    root = etree.fromstring(xml.encode())
    info = root.find("기본정보")
    name = (info.findtext("법령명_한글") or "").strip()
    kn = key_name(name)
    doc = LawDoc(name=name, key_name=kn, law_id=(info.findtext("법령ID") or "").strip(),
                 level=_level((info.findtext("법종구분") or "").strip()),
                 serial=(root.findtext(".//법령일련번호") or "").strip(),
                 effective_date=_date(info.findtext("시행일자")), promulgation_no=info.findtext("공포번호"))
    chapter = section = None
    for u in root.findall("조문/조문단위"):
        body = (u.findtext("조문내용") or "").strip()
        if (u.findtext("조문여부") or "").strip() == "전문":
            if _CHAPTER.match(body):
                chapter, section = body.strip(), None
            elif _SECTION.match(body):
                section = body.strip()
            continue
        no = int(u.findtext("조문번호") or 0)
        branch = int((u.findtext("조문가지번호") or "0").strip() or 0)
        title = (u.findtext("조문제목") or "").strip()
        akey = article_key(kn, no, branch)
        head = _HEAD.match(body)
        lead = clean(body[head.end():] if head else body)
        paras: list[Paragraph] = []
        if lead and not _is_deleted(lead):
            paras.append(Paragraph(key=f"{akey}:p0", no=0, text=lead))
        for p in u.findall("항"):
            mark = (p.findtext("항번호") or "").strip()
            text = _para_text(p)
            if not text:
                continue
            if mark and mark[0] in CIRCLED:
                n = CIRCLED.index(mark[0]) + 1
                paras.append(Paragraph(key=f"{akey}:p{n}", no=n, text=text))
            elif paras:                       # 항 번호 없이 호만 있는 경우: 본문에 붙인다
                paras[-1] = Paragraph(key=paras[-1].key, no=paras[-1].no, text=paras[-1].text + "\n" + text)
            else:
                paras.append(Paragraph(key=f"{akey}:p0", no=0, text=text))
        if not paras or (len(paras) == 1 and _is_deleted(paras[0].text)):
            continue
        doc.articles.append(Article(key=akey, law_id=doc.law_id, law_name=name, level=doc.level, no=no,
                                    branch=branch, title=title, chapter=chapter, section=section, paragraphs=paras,
                                    effective_date=_date(u.findtext("조문시행일자")) or doc.effective_date))
    doc.annexes = _annexes(root, doc)
    return doc


# ---------------- 행정규칙(admrul)
def split_paragraphs(akey: str, rest: str) -> list[Paragraph]:
    """'① …\\n  1. …\\n  ② …' → 항 목록. 항 머리 앞의 본문은 p0."""
    chunks = [c for c in _PARA_SPLIT.split(rest) if c.strip()]
    out = []
    for c in chunks:
        c = clean(c)
        if not c:
            continue
        if c[0] in CIRCLED:
            n = CIRCLED.index(c[0]) + 1
            out.append(Paragraph(key=f"{akey}:p{n}", no=n, text=c))
        else:
            out.append(Paragraph(key=f"{akey}:p0", no=0, text=c))
    return out


def parse_admrul(xml: str) -> LawDoc:
    root = etree.fromstring(xml.encode())
    info = root.find("행정규칙기본정보")
    name = (info.findtext("행정규칙명") or "").strip()
    kn = key_name(name)
    doc = LawDoc(name=name, key_name=kn, law_id=(info.findtext("행정규칙ID") or "").strip(),
                 level=_level((info.findtext("행정규칙종류") or "").strip()),
                 serial=(info.findtext("행정규칙일련번호") or "").strip(),
                 effective_date=_date(info.findtext("시행일자")), promulgation_no=info.findtext("발령번호"))
    chapter = section = None
    for el in root.findall("조문내용"):
        body = (el.text or "").strip()
        if not body:
            continue
        first = body.split("\n", 1)[0]
        if _CHAPTER.match(first) and not _HEAD.match(first):
            chapter, section = first.strip(), None
            continue
        if _SECTION.match(first) and not _HEAD.match(first):
            section = first.strip()
            continue
        head = _HEAD.match(body)
        if not head:
            continue
        no, branch = int(head["no"]), int(head["branch"] or 0)
        akey = article_key(kn, no, branch)
        paras = split_paragraphs(akey, body[head.end():])
        if not paras or (len(paras) == 1 and _is_deleted(paras[0].text)):
            continue
        doc.articles.append(Article(key=akey, law_id=doc.law_id, law_name=name, level=doc.level, no=no,
                                    branch=branch, title=(head["title"] or "").strip(), chapter=chapter,
                                    section=section, paragraphs=paras, effective_date=doc.effective_date))
    doc.annexes = _annexes(root, doc)
    return doc


# ---------------- 별표
def _annexes(root: etree._Element, doc: LawDoc) -> list[Annex]:
    out = []
    for u in root.findall(".//별표단위"):
        if (u.findtext("별표구분") or "").strip() != "별표":      # 서식은 넣지 않는다
            continue
        no = int((u.findtext("별표번호") or "0").strip() or 0)
        branch = int((u.findtext("별표가지번호") or "0").strip() or 0)
        title = (u.findtext("별표제목") or "").strip()
        text = clean(u.findtext("별표내용"))
        if not text or _is_deleted(text) or title.startswith("삭제"):
            continue
        rel = _RELATED.search(title)
        related = article_key(doc.key_name, int(rel["no"]), int(rel["branch"] or 0)) if rel else None
        out.append(Annex(key=annex_key(doc.key_name, no, branch), law_name=doc.name, title=title, text=text,
                         related_article=related))
    return out


def parse(xml: str) -> LawDoc:
    return parse_admrul(xml) if "<AdmRulService" in xml[:300] else parse_law(xml)
