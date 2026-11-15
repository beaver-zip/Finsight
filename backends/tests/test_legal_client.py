"""법령 API 클라이언트: OC 키 가리기와 재시도 분기."""
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.core.law import client
from app.settings import get_settings


def test_scrub_hides_oc_value_in_every_delimiter_form():
    for text in ('?OC=abc&amp;ID=1', '<a href="https://x?OC=abc">', "<link>?OC=abc</link>", "OC=abc ID=1"):
        assert "abc" not in client.scrub(text) and "OC=***" in client.scrub(text)


def _law_client(monkeypatch, handler):
    monkeypatch.setattr(get_settings(), "law_oc", SecretStr("secret-oc"))
    monkeypatch.setattr(client, "RETRY_DELAYS", (0, 0))
    return client.LawClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("codes, calls, ok", [([500, 200], 2, True), ([404], 1, False), ([503, 503, 503], 3, False)])
@pytest.mark.asyncio
async def test_get_retries_only_server_errors(monkeypatch, codes, calls, ok):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(codes[len(seen) - 1], text=f"<r>{request.url.query.decode()}</r>")

    c = _law_client(monkeypatch, handler)
    if ok:
        assert "secret-oc" not in await c._get("lawService.do", {"ID": "1"})
    else:
        with pytest.raises(client.LawApiError) as e:
            await c._get("lawService.do", {"ID": "1"})
        assert "secret-oc" not in str(e.value)
    assert len(seen) == calls


@pytest.mark.asyncio
async def test_failed_search_body_is_not_cached(monkeypatch):
    bodies = ["<LawSearch><resultCode>01</resultCode><resultMsg>bad key</resultMsg></LawSearch>",
              "<LawSearch><resultCode>00</resultCode></LawSearch>"]
    c = _law_client(monkeypatch, lambda request: httpx.Response(200, text=bodies.pop(0)))
    with pytest.raises(client.LawApiError):
        await c.search("law", "전자금융거래법")
    assert "<resultCode>00</resultCode>" in await c.search("law", "전자금융거래법")


@pytest.mark.parametrize("target, fixture", [("law", "law_act.xml"), ("admrul", "admrul_reg.xml"),
                                             ("lsDelegated", "delegated_act.xml")])
@pytest.mark.asyncio
async def test_failed_service_body_is_not_cached(monkeypatch, target, fixture):
    good = (Path(__file__).parent / "fixtures" / "lawgo" / fixture).read_text()
    bodies = ["<Law>사용자 정보 검증에 실패하였습니다.</Law>", good]
    c = _law_client(monkeypatch, lambda request: httpx.Response(200, text=bodies.pop(0)))
    with pytest.raises(client.LawApiError):
        await c.service(target, MST="1")
    assert await c.service(target, MST="1") == good
    assert await c.service(target, MST="1") == good and bodies == []
