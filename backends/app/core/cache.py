"""CacheBackend: 응답 캐시와 비용 장부를 한 SQLite 파일에 둔다."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


def make_key(*parts: Any) -> str:
    """임의 값들을 안정적인 해시 키로 만든다(dict 키 순서와 무관)."""
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class CacheBackend(ABC):
    @abstractmethod
    async def get(self, namespace: str, key: str) -> Any | None: ...

    @abstractmethod
    async def set(self, namespace: str, key: str, value: Any, ttl_s: float | None = None) -> None: ...

    @abstractmethod
    async def delete(self, namespace: str, key: str) -> None: ...

    @abstractmethod
    async def purge_expired(self) -> int: ...

    # 비용 장부
    @abstractmethod
    async def record_cost(self, entry: dict) -> None: ...

    @abstractmethod
    async def total_cost(self) -> float: ...

    @abstractmethod
    async def total_credits(self, service: str) -> int: ...


class SQLiteCache(CacheBackend):
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL,
                    PRIMARY KEY (namespace, key));
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL, service TEXT NOT NULL, model TEXT,
                    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0, cache_read_tokens INTEGER DEFAULT 0,
                    credits INTEGER DEFAULT 0, cost_usd REAL NOT NULL, meta TEXT);
                """
            )

    def _run(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    async def get(self, namespace: str, key: str) -> Any | None:
        rows = await asyncio.to_thread(
            self._run, "SELECT value, expires_at FROM kv WHERE namespace=? AND key=?", (namespace, key))
        if not rows:
            return None
        value, expires_at = rows[0]
        if expires_at is not None and expires_at < time.time():
            await self.delete(namespace, key)
            return None
        return json.loads(value)

    async def set(self, namespace: str, key: str, value: Any, ttl_s: float | None = None) -> None:
        now = time.time()
        await asyncio.to_thread(
            self._run,
            "INSERT OR REPLACE INTO kv(namespace, key, value, created_at, expires_at) VALUES (?,?,?,?,?)",
            (namespace, key, json.dumps(value, ensure_ascii=False, default=str), now,
             now + ttl_s if ttl_s else None))

    async def delete(self, namespace: str, key: str) -> None:
        await asyncio.to_thread(self._run, "DELETE FROM kv WHERE namespace=? AND key=?", (namespace, key))

    async def purge_expired(self) -> int:
        rows = await asyncio.to_thread(
            self._run, "DELETE FROM kv WHERE expires_at IS NOT NULL AND expires_at < ? RETURNING 1",
            (time.time(),))
        return len(rows)

    async def record_cost(self, entry: dict) -> None:
        await asyncio.to_thread(
            self._run,
            "INSERT INTO ledger(ts, service, model, input_tokens, output_tokens, cache_write_tokens,"
            " cache_read_tokens, credits, cost_usd, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), entry["service"], entry.get("model"), entry.get("input_tokens", 0),
             entry.get("output_tokens", 0), entry.get("cache_write_tokens", 0),
             entry.get("cache_read_tokens", 0), entry.get("credits", 0), entry["cost_usd"],
             json.dumps(entry.get("meta") or {}, ensure_ascii=False)))

    async def total_cost(self) -> float:
        rows = await asyncio.to_thread(self._run, "SELECT COALESCE(SUM(cost_usd), 0) FROM ledger")
        return float(rows[0][0])

    async def total_credits(self, service: str) -> int:
        rows = await asyncio.to_thread(
            self._run, "SELECT COALESCE(SUM(credits), 0) FROM ledger WHERE service=?", (service,))
        return int(rows[0][0])

    async def cost_by_service(self) -> dict[str, float]:
        rows = await asyncio.to_thread(
            self._run, "SELECT service, SUM(cost_usd) FROM ledger GROUP BY service")
        return {s: float(c) for s, c in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_cache: SQLiteCache | None = None


def get_cache() -> SQLiteCache:
    """프로세스당 하나. 앱 lifespan과 평가 러너가 같은 인스턴스를 쓴다."""
    global _cache
    if _cache is None:
        from app.settings import get_settings

        s = get_settings()
        _cache = SQLiteCache(s.resolve(s.cache_dir) / "cache.sqlite")
    return _cache


def set_cache(cache: SQLiteCache | None) -> None:
    """테스트용."""
    global _cache
    _cache = cache
