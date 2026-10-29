"""
FinTrack - formatting.py

Small helpers that turn numbers and dates into friendly text.
They are used both in Python and (registered as filters) inside templates.
"""

from datetime import date, datetime, timedelta


def inr(value, sign=False):
    """12345.5 -> ₹12,345.50 using Indian digit grouping (₹1,23,456)."""
    value = round(float(value or 0), 2)
    negative = value < 0
    absolute = abs(value)
    whole = int(absolute)
    paise = int(round((absolute - whole) * 100))
    if paise == 100:
        whole, paise = whole + 1, 0

    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    if paise:
        digits += f".{paise:02d}"

    prefix = "-" if negative else ("+" if sign and value > 0 else "")
    return f"{prefix}₹{digits}"


def plain_number(value):
    """Number for an <input>: 250.0 -> '250', 250.5 -> '250.5'."""
    value = round(float(value or 0), 2)
    return str(int(value)) if value == int(value) else f"{value:.2f}".rstrip("0")


def parse_date(text):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def fmt_date(iso_text, today=None):
    """'2026-10-09' -> 'Today' / 'Yesterday' / '8 Oct 2026'."""
    d = parse_date(iso_text)
    if d is None:
        return iso_text or ""
    today = today or date.today()
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return f"{d.day} {d.strftime('%b %Y')}"


def fmt_date_long(iso_text):
    d = parse_date(iso_text)
    if d is None:
        return iso_text or ""
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B %Y')}"


def fmt_time(hhmm):
    """'14:30' -> '2:30 PM'."""
    try:
        t = datetime.strptime(hhmm, "%H:%M")
    except (TypeError, ValueError):
        return hhmm or ""
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def pct_text(value):
    return f"{int(round(value))}%"
