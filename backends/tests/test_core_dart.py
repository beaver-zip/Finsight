"""OpenDART 모듈: 캐시 규칙, 상태 코드 처리, 보고서명·주식 수 파서."""
from __future__ import annotations

import io
import time
import zipfile

import httpx

from app.core.cache import SQLiteCache, set_cache
from app.core.dart import corp, shares
from app.core.dart.client import DartError, DartLimit, DartNoData


class FakeDart:
    """dart.json만 흉내 낸다. answers는 호출마다 하나씩 꺼내 쓰고, 예외면 던진다."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    async def json(self, endpoint, **params):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    binary = json


async def test_share_counts_empty_answer_expires(temp_cache):
    # 보고서 제출 직후 주식 총수가 아직 013이면 짧게만 기억하고, 데이터가 들어오면 다시 읽는다
    rows = [{"se": "보통주", "istc_totqy": "1,000", "tesstk_co": "100", "distb_stock_co": "900", "rcept_no": "r1"}]
    dart = FakeDart(DartNoData("013", "없음"), {"status": "000", "list": rows})
    assert await shares.share_counts(dart, "c", 2026, "11014") is None
    key = "stockTotqySttus:c:2026:11014"
    row = temp_cache._run("SELECT expires_at FROM kv WHERE namespace='dart.sh' AND key=?", (key,))
    assert row and row[0][0] is not None and row[0][0] - time.time() <= 300
    await temp_cache.set("dart.sh", key, [], ttl_s=-1)                    # 5분이 지난 것처럼
    got = await shares.share_counts(dart, "c", 2026, "11014")
    assert got is not None and got.common_out == 900 and dart.calls == 2
    assert await shares.share_counts(dart, "c", 2026, "11014") is not None and dart.calls == 2   # 정상 값은 계속 쓴다


async def test_corp_list_uses_expired_list_when_dart_is_down(temp_cache):
    # 하루가 지나 만료된 기업 목록이라도 OpenDART 점검 중에는 그것으로 검색한다
    rows = [{"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "", "modify_date": "",
             "stock_code": "005930"}]
    await temp_cache.set("dart.corplist", "all", rows, ttl_s=-1)
    dart = FakeDart(DartError("800", "시스템 점검"), DartError("800", "시스템 점검"))
    assert await corp.corp_list(dart) == rows
    assert [r["corp_code"] for r in await corp.search_keyword(dart, "삼성")] == ["00126380"]


async def test_corp_list_is_read_once_per_cache_and_parsed_from_zip(temp_cache, tmp_path):
    xml = ("<result><list><corp_code>00126380</corp_code><corp_name> 삼성전자 </corp_name><corp_eng_name>SAMSUNG"
           "</corp_eng_name><modify_date>20260101</modify_date><stock_code>005930</stock_code></list>"
           "<list><corp_code>00000001</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list></result>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml)
    dart = FakeDart(buf.getvalue())
    rows = await corp.corp_list(dart)
    assert rows == [{"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "SAMSUNG",
                     "modify_date": "20260101", "stock_code": "005930"},
                    {"corp_code": "00000001", "corp_name": "비상장", "corp_eng_name": "", "modify_date": "",
                     "stock_code": None}]
    await temp_cache.delete("dart.corplist", "all")
    assert await corp.corp_list(dart) is rows and dart.calls == 1        # 같은 캐시: SQLite를 다시 읽지 않는다
    other = SQLiteCache(tmp_path / "other.sqlite")
    set_cache(other)                                                     # 다른 캐시(다른 테스트·평가 러너)는 섞이지 않는다
    try:
        await other.set("dart.corplist", "all", rows[:1], ttl_s=60)
        assert await corp.corp_list(dart) == rows[:1]
    finally:
        set_cache(temp_cache)
        other.close()


# ---------- 보고서명·잠정실적·주식 수 파서 ----------

def test_parse_report_name_picks_report_code_by_fiscal_month():
    from app.core.dart.filings import parse_report_name as p

    assert p("사업보고서 (2025.12)", 12) == (2025, "11011")
    assert p("반기보고서 (2026.06)", 12) == (2026, "11012")
    assert p("분기보고서 (2026.03)", 12) == (2026, "11013")
    assert p("[기재정정]분기보고서 (2026.09)", None) == (2026, "11014")     # 결산월 없으면 12월
    assert p("분기보고서 (2026.06)", 3) == (2026, "11013")                 # 3월 결산: 6월이 1분기
    assert p("분기보고서 (2026.12)", 3) == (2026, "11014")
    assert p("분기보고서 (2026.06)", 12) is None                           # 결산월과 맞지 않는 분기
    assert p("주요사항보고서(자기주식취득결정)", 12) is None


def test_parse_prelim_applies_unit_multiplier():
    from datetime import date
    from decimal import Decimal

    from app.core.dart.prelim import PrelimFiling, parse_prelim

    cells = ["(단위 : 억원, %)", "당기실적", "2026-07-01", "2026-09-30",
             "매출액", "당해실적", "1,000", "900", "11.1", "-", "800", "25.0", "-",
             "누계실적", "3,000", "-", "-", "-", "2,500", "20.0", "-",
             "영업이익", "당해실적", "△50", "10", "-", "적자전환", "20", "-", "-"]
    f = PrelimFiling("r1", date(2026, 10, 28), "연결재무제표기준영업(잠정)실적(공정공시)", False)
    p = parse_prelim(cells, f)
    e8 = Decimal(10) ** 8
    assert (p.year, p.q, p.unit, p.consolidated, p.key) == (2026, 3, "억원", True, "3Q26")
    rev = p.rows["revenue"]
    assert (rev.cur, rev.prev, rev.qoq, rev.yoy_base, rev.yoy) == (1000 * e8, 900 * e8, 11.1, 800 * e8, 25.0)
    assert (rev.cum, rev.cum_prev, rev.cum_yoy) == (3000 * e8, 2500 * e8, 20.0)
    op = p.rows["operating_income"]
    assert (op.cur, op.qoq, op.note) == (-50 * e8, None, "적자전환")


async def test_share_counts_sums_common_and_preferred_and_skips_total(temp_cache):
    rows = [{"se": "보통주", "istc_totqy": "1,000", "tesstk_co": "100", "distb_stock_co": "900", "rcept_no": "r"},
            {"se": "우선주", "istc_totqy": "200", "tesstk_co": "0", "distb_stock_co": "-"},     # 유통 칸 비면 발행−자기
            {"se": "합계", "istc_totqy": "1,200", "tesstk_co": "100", "distb_stock_co": "1,100"}]
    sh = await shares.share_counts(FakeDart({"status": "000", "list": rows}), "c", 2025, "11011")
    assert (sh.common_issued, sh.common_out, sh.pref_issued, sh.pref_out, sh.total_out) == (1000, 900, 200, 200, 1100)
    only_pref = [{"se": "의결권없는 주식", "istc_totqy": "10", "tesstk_co": "0", "distb_stock_co": "10"}]
    assert await shares.share_counts(FakeDart({"status": "000", "list": only_pref}), "d", 2025, "11011") is None


# ---------- DartClient 상태 코드·재시도 ----------

async def _dart_calls(monkeypatch, *answers):
    """MockTransport가 answers를 차례로 돌려주는 DartClient로 list.json을 한 번 부른다 → (결과 또는 예외, 호출 수)."""
    from app.core.dart import client

    monkeypatch.setattr(client, "require", lambda value, name: "K")
    monkeypatch.setattr(client, "RETRY_DELAYS", (0, 0, 0))
    queue, calls = list(answers), []

    def handler(req):
        calls.append(1)
        a = queue.pop(0)
        return httpx.Response(200, json={"status": a, "message": a}) if isinstance(a, str) else httpx.Response(a)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        try:
            out = await client.DartClient(http).json("list")
        except client.DartError as e:
            out = e
    return out, len(calls)


async def test_dart_client_status_codes_and_retries(monkeypatch):
    out, n = await _dart_calls(monkeypatch, "000")
    assert out["status"] == "000" and n == 1
    out, n = await _dart_calls(monkeypatch, "013")
    assert isinstance(out, DartNoData) and n == 1
    out, n = await _dart_calls(monkeypatch, "020")
    assert isinstance(out, DartLimit) and n == 1                       # 한도 초과는 다시 부르지 않는다
    out, n = await _dart_calls(monkeypatch, "800", "000")
    assert out["status"] == "000" and n == 2                           # 점검은 기다렸다가 다시 부른다
    out, n = await _dart_calls(monkeypatch, 503, "000")
    assert out["status"] == "000" and n == 2
    out, n = await _dart_calls(monkeypatch, 503, 503, 503, 503)
    assert isinstance(out, DartError) and out.status == "503" and n == 4
    out, n = await _dart_calls(monkeypatch, 404)
    assert isinstance(out, DartError) and out.status == "404" and n == 1   # 4xx는 다시 불러도 같다
