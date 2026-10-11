"""법령 답변 변환·렌더링과 서브그래프 분기(LLM·DB 없이)."""
from __future__ import annotations

from types import SimpleNamespace as NS

from app.agents.legal.answer import points_from, render_markdown
from app.agents.legal.graph import LegalConfig, after_judge
from app.agents.legal.tools import normalize, short_name
from app.core.law.corpus import load_corpus
from app.domain.models import LegalAnswer


def _cite(source, idx, text):
    return NS(type="search_result_location", source=source, start_block_index=idx, end_block_index=idx + 1,
              cited_text=text)


def test_points_drop_verbatim_quotes_and_map_paragraphs():
    c = load_corpus()
    docs = [c.get("전자금융거래법:21-2"), c.get("전자금융거래법시행령:11-4")]
    p2 = docs[0].paragraphs[1].text
    resp = NS(content=[
        NS(type="thinking"),
        NS(type="text", text="결론 문장이다.\n\n법은 범위를 대통령령에 위임한다 (", citations=None),
        NS(type="text", text=p2[2:], citations=[_cite("전자금융거래법:21-2", 1, p2)]),
        NS(type="text", text=").\n\n시행령은 총자산 2조원 이상을 기준으로 정한다.",
           citations=[_cite("전자금융거래법시행령:11-4", 0, docs[1].paragraphs[0].text)]),
    ])
    pts = points_from(resp, docs)
    assert [p.text for p in pts] == ["결론 문장이다.", "법은 범위를 대통령령에 위임한다.",
                                     "시행령은 총자산 2조원 이상을 기준으로 정한다."]
    assert [(x.source, x.paragraph_index) for x in pts[1].citations] == [("전자금융거래법:21-2", 2)]
    assert pts[2].citations[0].paragraph_index == 1


def test_render_markdown_sections_and_refusal():
    c = load_corpus()
    resp = NS(content=[NS(type="text", text="요약.\n\n상세.", citations=[
        _cite("전자금융거래법시행령:11-4", 3, "④ 법 제21조의2제5항에 따른 정보보호최고책임자의 자격요건은 별표 1과 같다.")])])
    pts = points_from(resp, [c.get("전자금융거래법시행령:11-4")])
    md = render_markdown(LegalAnswer(answerable=True, points=pts, paths=[["전자금융거래법:21-2",
                         "전자금융거래법시행령:11-4", "전자금융거래법시행령:annex-1"]], notes=["현행 조문(시행일 2026-04-28) 기준"]), c)
    assert md.startswith("### 핵심 요약\n요약.") and "### 상세 내용\n상세.[1]" in md
    assert "조문 연결: 법 제21조의2 → 시행령 제11조의4 → 시행령 별표 1" in md
    assert "1. [시행령 제11조의4제4항 " in md and "※ 현행 조문" in md
    ref = render_markdown(LegalAnswer(answerable=False, fallback=["전자금융감독규정:15"]), c)
    assert "찾지 못했습니다" in ref and "전자금융감독규정 제15조" in ref


def test_names_and_routing():
    assert normalize("전금법 시행령상 CISO 겸직") == "전자금융거래법 시행령상 정보보호최고책임자 겸직"
    assert normalize("감독규정 제15조") == "전자금융감독규정 제15조"
    assert short_name("전자금융감독규정시행세칙:2-3") == "시행세칙 제2조의3"
    cfg = {"configurable": {"legal_config": LegalConfig(max_loops=2)}}
    assert after_judge({"missing": [], "loops": 0}, cfg) == "answer"
    assert after_judge({"missing": ["x"], "loops": 2}, cfg) == "retrieve"
    assert after_judge({"missing": ["x"], "loops": 3}, cfg) == "refuse"
