import datetime as datetime

from chat.chat import _iso_or_none


def test_iso_or_none_accepts_datetime():
    value = datetime.datetime(2026, 4, 27, 10, 0, 0, tzinfo=datetime.timezone.utc)
    assert _iso_or_none(value) == value.isoformat()


def test_iso_or_none_accepts_iso_string():
    assert _iso_or_none("2026-04-27 10:00:00") == "2026-04-27T10:00:00"
