from datetime import datetime

import pytest

from connor.timeparse import extract_when, format_dt, parse_when

NOW = datetime(2026, 11, 15, 19, 40)  # Sunday


@pytest.mark.parametrize(
    "text, expected, rest",
    [
        ("morgen 18:00 Mama anrufen", datetime(2026, 11, 16, 18, 0), "Mama anrufen"),
        ("in 2 Stunden Pizza holen", datetime(2026, 11, 15, 21, 40), "Pizza holen"),
        ("in 10 Minuten Tee", datetime(2026, 11, 15, 19, 50), "Tee"),
        ("in einer halben Stunde Pause", datetime(2026, 11, 15, 20, 10), "Pause"),
        ("am 20.11. um 9 Uhr Zahnarzt", datetime(2026, 11, 20, 9, 0), "Zahnarzt"),
        ("16.11.2026 18:30", datetime(2026, 11, 16, 18, 30), ""),
        ("2026-11-16 07:30", datetime(2026, 11, 16, 7, 30), ""),
        ("2026-11-16T07:30:00", datetime(2026, 11, 16, 7, 30), ""),
        ("Freitag abends um 8 Kino", datetime(2026, 11, 20, 20, 0), "Kino"),
        ("heute Abend um halb 9 Serie", datetime(2026, 11, 15, 20, 30), "Serie"),
        ("übermorgen früh Bus", datetime(2026, 11, 17, 8, 0), "Bus"),
        ("um 9 Training", datetime(2026, 11, 16, 9, 0), "Training"),
        ("um 20 Uhr Film", datetime(2026, 11, 15, 20, 0), "Film"),
        ("in 3 Tagen um 10 Uhr Arzt", datetime(2026, 11, 18, 10, 0), "Arzt"),
        ("am 1.1. Neujahr", datetime(2027, 1, 1, 9, 0), "Neujahr"),
        ("Sonntag 10 Uhr Brunch", datetime(2026, 11, 22, 10, 0), "Brunch"),
    ],
)
def test_extract_when(text, expected, rest):
    when, remaining = extract_when(text, NOW)
    assert when == expected
    assert remaining == rest


def test_nothing_found():
    assert extract_when("Kapitel drei überarbeiten", NOW) == (None, "Kapitel drei überarbeiten")
    assert parse_when("", NOW) is None


def test_invalid_date_is_ignored():
    assert parse_when("am 31.02. um 10 Uhr", NOW) == datetime(2026, 11, 16, 10, 0)


def test_format():
    assert format_dt(datetime(2026, 11, 16, 18, 0)) == "Mo, 16.11.2026, 18:00 Uhr"
