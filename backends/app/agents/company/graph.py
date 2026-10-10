"""기업 분석 그래프(리포트 v2): 정기보고서 확인 → 병렬 3갈래(수집·공시·뉴스) → 분석(컨센서스 포함) → 규제 트리거
→ 본문 작성 → 검증 ⇄ 재작성(최대 2회) → 조립."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.company import nodes
from app.agents.company.state import CompanyState


def build_company_graph(checkpointer=None):
    g = StateGraph(CompanyState)
    g.add_node("get_filing", nodes.get_filing)
    g.add_node("collect", nodes.collect_data)
    g.add_node("get_disclosures", nodes.get_disclosures)
    g.add_node("get_news", nodes.get_news)
    g.add_node("analyze", nodes.analyze_data)
    g.add_node("regulatory_check", nodes.regulatory_check)
    g.add_node("write", nodes.write_report)
    g.add_node("verify", nodes.verify_draft)
    g.add_node("revise", nodes.revise_draft)
    g.add_node("assemble", nodes.assemble)

    g.add_edge(START, "get_filing")
    for branch in ("collect", "get_disclosures", "get_news"):
        g.add_edge("get_filing", branch)
    g.add_edge(["collect", "get_disclosures", "get_news"], "analyze")
    g.add_edge("analyze", "regulatory_check")
    g.add_edge("regulatory_check", "write")
    g.add_edge("write", "verify")
    g.add_conditional_edges("verify", nodes.after_verify, {"revise": "revise", "assemble": "assemble"})
    g.add_edge("revise", "verify")
    g.add_edge("assemble", END)
    return g.compile(checkpointer=checkpointer, name="company")
