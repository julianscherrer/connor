"""Understands German time expressions like «morgen um 18 Uhr» or «in 2 Stunden»."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")
WEEKDAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
WEEKDAY_LONG = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

_NUMBER_WORDS = {
    "ein": 1, "eine": 1, "einer": 1, "einem": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
    "zwölf": 12, "zwoelf": 12, "fünfzehn": 15, "zwanzig": 20, "dreissig": 30, "vierzig": 40,
    "fünfzig": 50, "sechzig": 60,
}
_NUM = r"(\d+|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")"

_PARTS_OF_DAY = {
    "früh": 8, "frueh": 8, "morgens": 8, "am morgen": 8,
    "vormittags": 10, "am vormittag": 10, "vormittag": 10,
    "mittags": 12, "am mittag": 12, "mittag": 12,
    "nachmittags": 15, "am nachmittag": 15, "nachmittag": 15,
    "abends": 19, "am abend": 19, "abend": 19,
    "nachts": 22, "in der nacht": 22, "nacht": 22,
}
_PM_PARTS = {
    "nachmittags", "am nachmittag", "nachmittag", "abends", "am abend", "abend",
    "nachts", "in der nacht", "nacht",
}

_I = re.IGNORECASE
_RE_REL_FRACTION = re.compile(r"\bin\s+(?:einer\s+)?(halben\s+stunde|viertelstunde|dreiviertelstunde)\b", _I)
_RE_REL = re.compile(
    r"\bin\s+" + _NUM + r"\s*(minuten|minute|min|stunden|stunde|std|h|tagen|tag|wochen|woche)\b", _I
)
_RE_ISO = re.compile(
    r"\b(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t](\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?)?(?:z|[+-]\d{2}:?\d{2})?", _I
)
_RE_DATE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.(\d{4}|\d{2})?(?!\d)")
_RE_PART = re.compile(
    r"\b(am\s+morgen|am\s+vormittag|am\s+mittag|am\s+nachmittag|am\s+abend|in\s+der\s+nacht|"
    r"früh|frueh|morgens|vormittags|vormittag|mittags|mittag|nachmittags|nachmittag|"
    r"abends|abend|nachts|nacht)\b",
    _I,
)
_RE_DAYWORD = re.compile(r"\b(übermorgen|uebermorgen|morgen|heute)\b", _I)
_RE_WEEKDAY = re.compile(
    r"\b(?:(nächsten|naechsten|nächste|naechste|kommenden|kommende)\s+)?(" + "|".join(WEEKDAYS) + r")\b", _I
)
_RE_TIME_COLON = re.compile(r"\b(?:um\s+)?([01]?\d|2[0-3])[:.]([0-5]\d)(?:\s*uhr)?\b", _I)
_RE_TIME_UHR = re.compile(r"\b(?:um\s+)?([01]?\d|2[0-3])\s*uhr(?:\s+([0-5]\d)\b)?", _I)
_RE_TIME_HALF = re.compile(r"\b(?:um\s+)?halb\s+(\d{1,2})\b", _I)
_RE_TIME_UM = re.compile(r"\bum\s+([01]?\d|2[0-3])\b(?![.:]?\d)", _I)
_RE_EDGE = re.compile(r"^(?:[\s\-–:|,;.]|\b(?:am|um)\b)+|(?:[\s\-–:|,;]|\b(?:am|um)\b)+$", _I)


def _number(token: str) -> int:
    token = token.lower()
    return int(token) if token.isdigit() else _NUMBER_WORDS.get(token, 1)


def extract_when(text: str, now: datetime | None = None) -> tuple[datetime | None, str]:
    """Finds a point in time in ``text``. Returns (datetime or None, remaining text)."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    work = text

    def blank(match: re.Match) -> None:
        nonlocal work
        work = work[: match.start()] + " " * (match.end() - match.start()) + work[match.end():]

    def rest() -> str:
        cleaned = " ".join(work.split())
        previous = None
        while previous != cleaned:
            previous = cleaned
            cleaned = _RE_EDGE.sub("", cleaned).strip()
        return cleaned

    day: date | None = None

    m = _RE_REL_FRACTION.search(work)
    if m:
        minutes = {"halben stunde": 30, "viertelstunde": 15, "dreiviertelstunde": 45}
        key = " ".join(m.group(1).lower().split())
        blank(m)
        return now + timedelta(minutes=minutes[key]), rest()

    m = _RE_REL.search(work)
    if m:
        amount = _number(m.group(1))
        unit = m.group(2).lower()
        blank(m)
        if unit.startswith(("min",)):
            return now + timedelta(minutes=amount), rest()
        if unit in ("stunden", "stunde", "std", "h"):
            return now + timedelta(hours=amount), rest()
        day = now.date() + (timedelta(days=amount) if unit.startswith("tag") else timedelta(weeks=amount))

    explicit_time: time | None = None
    m = _RE_ISO.search(work)
    if m:
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if m.group(4):
                explicit_time = time(int(m.group(4)), int(m.group(5)))
            blank(m)
        except ValueError:
            pass

    if day is None:
        for m in _RE_DATE.finditer(work):
            day_num, month = int(m.group(1)), int(m.group(2))
            year_raw = m.group(3)
            year = int(year_raw) + (2000 if year_raw and len(year_raw) == 2 else 0) if year_raw else now.year
            try:
                candidate = date(year, month, day_num)
            except ValueError:
                continue
            if not year_raw and candidate < now.date():
                try:
                    candidate = date(year + 1, month, day_num)
                except ValueError:
                    continue
            day = candidate
            blank(m)
            break

    part_hour: int | None = None
    pm = False
    m = _RE_PART.search(work)
    if m:
        key = " ".join(m.group(1).lower().split())
        part_hour = _PARTS_OF_DAY[key]
        pm = key in _PM_PARTS
        blank(m)

    m = _RE_DAYWORD.search(work)
    if m:
        offsets = {"heute": 0, "morgen": 1, "übermorgen": 2, "uebermorgen": 2}
        if day is None:
            day = now.date() + timedelta(days=offsets[m.group(1).lower()])
        blank(m)

    weekday: int | None = None
    next_week = False
    m = _RE_WEEKDAY.search(work)
    if m:
        weekday = WEEKDAYS.index(m.group(2).lower())
        next_week = bool(m.group(1))
        blank(m)

    if explicit_time is None:
        m = _RE_TIME_COLON.search(work)
        if m:
            explicit_time = time(int(m.group(1)), int(m.group(2)))
            blank(m)
    if explicit_time is None:
        m = _RE_TIME_UHR.search(work)
        if m:
            explicit_time = time(int(m.group(1)), int(m.group(2) or 0))
            blank(m)
    if explicit_time is None:
        m = _RE_TIME_HALF.search(work)
        if m:
            hour = int(m.group(1))
            if 1 <= hour <= 24:
                explicit_time = time((hour - 1) % 24, 30)
                blank(m)
    if explicit_time is None:
        m = _RE_TIME_UM.search(work)
        if m:
            explicit_time = time(int(m.group(1)), 0)
            blank(m)

    if explicit_time is not None and pm and explicit_time.hour < 12:
        explicit_time = explicit_time.replace(hour=explicit_time.hour + 12)

    at = explicit_time or (time(part_hour, 0) if part_hour is not None else None)

    if day is None and weekday is not None:
        ahead = (weekday - now.weekday()) % 7
        if ahead == 0 and (next_week or datetime.combine(now.date(), at or time(9, 0)) <= now):
            ahead = 7
        day = now.date() + timedelta(days=ahead)

    if day is None and at is None:
        return None, text.strip()
    if day is None:
        result = datetime.combine(now.date(), at)
        if result <= now:
            result += timedelta(days=1)
        return result, rest()
    return datetime.combine(day, at or time(9, 0)), rest()


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    return extract_when(text, now)[0]


def format_dt(value: datetime) -> str:
    return f"{WEEKDAY_SHORT[value.weekday()]}, {value:%d.%m.%Y}, {value:%H:%M} Uhr"
