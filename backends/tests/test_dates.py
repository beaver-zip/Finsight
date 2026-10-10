from datetime import datetime

from app.domain.dates import KST, parse_published

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=KST)


def test_rfc2822_to_kst():
    d = parse_published("Tue, 11 Mar 2025 17:00:00 GMT")
    assert d == datetime(2025, 3, 12, 2, 0, tzinfo=KST)


def test_relative_and_dotted():
    assert parse_published("19 hours ago", now=NOW) == datetime(2026, 10, 9, 17, 0, tzinfo=KST)
    assert parse_published("3일 전", now=NOW) == datetime(2026, 10, 7, 12, 0, tzinfo=KST)
    assert parse_published("2025.01.15.") == datetime(2025, 1, 15, tzinfo=KST)


def test_unparseable():
    assert parse_published(None) is None
    assert parse_published("어제쯤") is None
