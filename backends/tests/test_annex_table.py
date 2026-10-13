"""별표 괘선 표 → 마크다운 표, 표 행 단위 청크."""
from __future__ import annotations

from pathlib import Path

from app.core.law.parser import parse
from app.core.law.vector_store import ANNEX_CHUNK, annex_chunks
from app.domain.annex_table import Vocab, convert, table_blocks
from app.domain.models import Annex

FIX = Path(__file__).parent / "fixtures" / "lawgo"

BOX = """■ 시행령 [별표 2]
  과징금(제26조제2항 관련)
┏━━━━━━━━━━━━━━━━━━━┯━━━━━━━━━━┯━━━━┓
┃위반행위                              │근거 법조문         │과징금  ┃
┠───────────────────┼──────────┼────┨
┃1. 금융회사가 법 제6조제1항을 위반한  │법 제43조제2항제1호 │3천만원 ┃
┃때                                    │법 제46조제2항      │        ┃
┠───────────────────┼──────────┼────┨
┃2. 금융회사 또는 전자금융업자가 법 제 │법 제43조제2항제2호 │1천만원 ┃
┃8조제2항을 위반하여 오류를            │                    │        ┃
┃조사하지 아니한 때                    │                    │        ┃
┗━━━━━━━━━━━━━━━━━━━┷━━━━━━━━━━┷━━━━┛
※ 비고"""


def test_box_table_to_markdown():
    md = convert(BOX)
    lines = md.split("\n")
    assert lines[0] == "■ 시행령 [별표 2]" and lines[-1] == "※ 비고"           # 표 밖 글은 그대로
    assert "| 위반행위 | 근거 법조문 | 과징금 |" in lines and "|---|---|---|" in lines
    row1 = next(x for x in lines if x.startswith("| 1."))
    assert row1 == "| 1. 금융회사가 법 제6조제1항을 위반한 때 | 법 제43조제2항제1호 법 제46조제2항 | 3천만원 |"
    row2 = next(x for x in lines if x.startswith("| 2."))
    assert "법 제8조제2항을 위반하여 오류를 조사하지 아니한 때" in row2     # 칸을 꽉 채운 줄은 붙이고, 여백이 있으면 띄운다
    assert convert(md) == md                                               # 두 번 불러도 같다


def test_vocab_glues_words_split_mid_line():
    box = """┌──────────┬───┐
│항목                │금액  │
├──────────┼───┤
│법 제19조를 위      │600   │
│반한 경우           │      │
└──────────┴───┘"""
    assert "위 반한" in convert(box)                       # 여백만 보면 띄운다
    v = Vocab.build(["이 법을 위반한 자는 과태료를 낸다. 위반한 경우에도 같다."])
    assert "| 법 제19조를 위반한 경우 | 600 |" in convert(box, v)


def test_table_rows_chunked_with_header():
    rows = "\n".join(f"| {i}. 위반행위 {'가' * 60} | 법 제{i}조 | {i}00 |" for i in range(1, 40))
    text = f"제목\n| 위반행위 | 근거 | 금액 |\n|---|---|---|\n{rows}\n비고"
    blocks = table_blocks(text)
    assert [b[0] == "" for b in blocks] == [True, False, True]
    chunks = annex_chunks(Annex(key="법:annex-2", law_name="법", title="과태료", text=text), "법률")
    assert len(chunks) > 1 and all(len(c.text) <= ANNEX_CHUNK + 200 for c in chunks)
    assert all(c.text.count("| 위반행위 | 근거 | 금액 |") == 1 for c in chunks[1:])   # 조각마다 머리 행


def test_parser_converts_fixture_annexes():
    doc = parse((FIX / "law_decree.xml").read_text())
    tables = [a for a in doc.annexes if "|---" in a.text]
    assert tables and not any("┃" in a.text or "│" in a.text for a in tables)
