"""기업 분석 그래프: 병렬 4갈래(재무·공시·주가·뉴스) → 규제 트리거 → 결론 → 검증 ⇄ 재작성(최대 2회) → 조립."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.company import nodes
from app.agents.company.state import CompanyState


def build_company_graph(checkpointer=None):
    g = StateGraph(CompanyState)
    g.add_node("get_filing", nodes.get_filing)
    g.add_node("get_financials", nodes.get_financials)
    g.add_node("get_disclosures", nodes.get_disclosures)
    g.add_node("get_price", nodes.get_price)
    g.add_node("get_news", nodes.get_news)
    g.add_node("regulatory_check", nodes.regulatory_check)
    g.add_node("conclusion", nodes.write_conclusion)
    g.add_node("verify", nodes.verify_draft)
    g.add_node("revise", nodes.revise_draft)
    g.add_node("assemble", nodes.assemble)

    g.add_edge(START, "get_filing")
    for branch in ("get_financials", "get_disclosures", "get_price", "get_news"):
        g.add_edge("get_filing", branch)
    g.add_edge(["get_financials", "get_disclosures", "get_price", "get_news"], "regulatory_check")
    g.add_edge("regulatory_check", "conclusion")
    g.add_edge("conclusion", "verify")
    g.add_conditional_edges("verify", nodes.after_verify, {"revise": "revise", "assemble": "assemble"})
    g.add_edge("revise", "verify")
    g.add_edge("assemble", END)
    return g.compile(checkpointer=checkpointer, name="company")
