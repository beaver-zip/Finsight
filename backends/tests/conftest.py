import pytest

from app.core.cache import SQLiteCache, set_cache


@pytest.fixture(autouse=True)
def temp_cache(tmp_path):
    c = SQLiteCache(tmp_path / "cache.sqlite")
    set_cache(c)
    yield c
    c.close()
    set_cache(None)


@pytest.fixture
def real_fonts():
    """이 픽스처를 받는 테스트만 실제 폰트 서브셋(굵기당 약 1초)을 만든다."""


@pytest.fixture(autouse=True)
def stub_font_faces(request, monkeypatch):
    if "real_fonts" not in request.fixturenames:
        monkeypatch.setattr("app.agents.company.render.font_faces", lambda _: "")
