"""리포트 API의 예외 → HTTP 상태·SSE 코드 매핑."""
import pytest

from app.api.report import _http_error, error_code
from app.core import budget
from app.core.dart.client import DartError, DartLimit


@pytest.mark.parametrize("exc, status, detail, code", [
    (DartLimit("020", "limit"), 429, "OpenDART 요청 한도를 넘었습니다", "DART_LIMIT"),
    (budget.BudgetExceeded("over"), 402, "over", "BUDGET"),
    (budget.TokenBudgetExceeded("tokens"), 429, "tokens", "TOKEN_BUDGET"),
    (DartError("800", "maintenance"), 502, "OpenDART 800: maintenance", "INTERNAL"),
    (ValueError("boom"), 500, "보고서 생성 중 오류: boom", "INTERNAL"),
])
def test_report_errors_map_to_status_and_code(exc, status, detail, code):
    e = _http_error(exc)
    assert (e.status_code, e.detail) == (status, detail)
    assert error_code(exc) == code
