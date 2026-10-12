"""부문 실적: 연결재무제표 주석 '영업부문에 대한 공시' 표와 사업의 내용 글 표 읽기."""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.dart.segments import parse_notes, parse_segments

FIX = Path(__file__).parent / "fixtures" / "segments"
M = Decimal(10) ** 6


def _notes(name: str):
    return parse_notes((FIX / f"{name}.html").read_text())


def test_columns_with_total_and_external_rows():
    # CJ제일제당: '총 매출액'(내부거래 포함)을 '수익'(외부)보다 먼저, 3개월·누적 중 누적, 부문간 제거 열은 뺀다
    seg, unit = _notes("cj")
    assert unit == "천원" and list(seg) == ["식품", "바이오", "물류"]
    assert seg["식품"] == {"rev": Decimal(8_757_126_528) * 1000, "op": Decimal(216_545_674) * 1000}


def test_cumulative_columns_and_negative_parentheses():
    seg, unit = _notes("lge")
    assert unit == "백만원" and len(seg) == 6 and "부문 합계" not in seg
    assert seg["Home Appliance Solution"]["rev"] == 13_816_989 * M
    assert seg["기타부문"]["op"] == -22_131 * M


def test_simple_columns_gs_retail():
    seg, _ = _notes("gs")
    assert seg == {"편의점": {"rev": 4_470_737 * M, "op": 92_711 * M},
                   "수퍼마켓": {"rev": 923_217 * M, "op": 21_339 * M},
                   "홈쇼핑": {"rev": 530_209 * M, "op": 56_432 * M},
                   "공통및기타": {"rev": 105_856 * M, "op": -2_801 * M}}


def test_segment_name_above_sub_columns_and_profit_row():
    # 포스코홀딩스: 부문 아래 '영업부문·내부매출액·합계' 하위 열, 영업이익 대신 '부문이익(손실)'
    seg, _ = _notes("posco")
    assert list(seg) == ["철강", "무역", "건설", "물류 등", "이차전지소재", "기타"]
    assert seg["철강"] == {"rev": 30_356_911 * M, "op": 351_821 * M}


def test_footnote_marks_and_adjustment_column():
    seg, _ = _notes("emart")
    assert "유통업" in seg and "건설업" in seg and not any("조정" in k or "*" in k for k in seg)
    assert seg["건설업"]["op"] == -31_443 * M


def test_no_operating_income_row_gives_none():
    # 카카오: 부문 세전이익·순이익만 있어 영업이익을 만들지 않는다
    assert _notes("kakao") is None


def test_prior_period_table_is_skipped():
    page = (FIX / "gs.html").read_text()
    first = page.index("<TABLE border='1'>")
    end = page.index("</TABLE>", first) + len("</TABLE>")
    assert parse_notes(page[:first] + page[end:]) is None


@pytest.mark.parametrize("junk", ["(872,607) (2.8) 합계", "합계"])
def test_business_text_rejects_total_and_number_names(junk):
    text = ("사업부문별 요약 재무 현황 (단위 : 억원) 부문 구분 금액 비중 백화점 매출액 1,000 50% 영업이익 100 "
            f"할인점 매출액 800 40% 영업이익 -20 {junk} 매출액 1,800 100% 영업이익 80")
    seg, unit = parse_segments(text)
    assert unit == "억원" and set(seg) == {"백화점", "할인점"}
    assert not any(re.search(r"\d", k) for k in seg)
