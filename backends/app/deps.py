"""lifespan에서 만든 공유 객체를 라우터에 주입한다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Request

from app.core.dart.client import DartClient


@dataclass
class AppState:
    http: httpx.AsyncClient
    dart: DartClient
    company_graph: Any
    legal_runner: Any = None
    law_tools: Any = None
    chat_graph: Any = None


def get_state(request: Request) -> AppState:
    return request.app.state.fs


def get_dart(request: Request) -> DartClient:
    return request.app.state.fs.dart
