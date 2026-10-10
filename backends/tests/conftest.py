import pytest

from app.core.cache import SQLiteCache, set_cache


@pytest.fixture(autouse=True)
def temp_cache(tmp_path):
    c = SQLiteCache(tmp_path / "cache.sqlite")
    set_cache(c)
    yield c
    c.close()
    set_cache(None)
