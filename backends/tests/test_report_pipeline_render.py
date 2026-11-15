"""리포트 조립의 렌더링(폰트 서브셋 포함)이 이벤트 루프를 막지 않는지, 실제 폰트 내장 경로."""
import asyncio
import time

import pytest

from app.agents.company import nodes, render
from app.agents.company.analysis import ReportData


@pytest.mark.asyncio
async def test_assemble_renders_off_the_event_loop(monkeypatch):
    def slow_render(*args, **kwargs):
        time.sleep(0.3)                 # 폰트 서브셋처럼 CPU를 오래 쓰는 동기 작업
        return "<html>"

    monkeypatch.setattr(nodes, "render_report", slow_render)
    data = ReportData(corp={"name": "테스트"}, made="2026-11-11", asof=None, latest={}, table_div=1e8, table_unit="억원")
    started = time.perf_counter()
    task = asyncio.ensure_future(nodes.assemble({"data": data, "draft": None}, {}))
    await asyncio.sleep(0.05)
    assert time.perf_counter() - started < 0.2 and not task.done()   # 렌더 중에도 루프가 돈다
    assert (await task)["html"] == "<html>"


def test_real_font_subset_is_embedded(real_fonts):
    css = render.font_faces("삼성전자 영업이익 9.1조원")
    assert css.count("@font-face") == 4 and "data:font/woff2;base64," in css
