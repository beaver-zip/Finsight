"""법령 API 클라이언트: OC 키 가리기와 재시도 분기."""
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
