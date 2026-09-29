"""Small helpers that turn numbers and months into the text shown on screen."""
from __future__ import annotations

import calendar


def month_label(month: str, short: bool = False) -> str:
    """'2026-08' -> 'August 2026' (or 'Aug 2026' when short=True)."""
    year, mon = int(month[:4]), int(month[5:7])
    name = calendar.month_abbr[mon] if short else calendar.month_name[mon]
    return f"{name} {year}"


def money(value: float | None, decimals: int = 2) -> str:
    """185 -> '$185.00'; -5 -> '-$5.00'."""
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def signed_money(value: float | None, decimals: int = 2) -> str:
    """5 -> '+$5.00'; -5 -> '-$5.00'."""
    if value is None:
        return "n/a"
    return ("+" if value >= 0 else "") + money(value, decimals)


def money_short(value: float | None) -> str:
    """1_240_000 -> '$1.24M'; 245_300 -> '$245.3K'."""
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:,.2f}M"
    if v >= 10_000:
        return f"{sign}${v / 1_000:,.1f}K"
    return f"{sign}${v:,.0f}"


def pct(fraction: float | None, decimals: int = 1) -> str:
    """0.76 -> '76.0%'."""
    if fraction is None:
        return "n/a"
    return f"{fraction * 100:.{decimals}f}%"


def signed_pct(fraction: float | None, decimals: int = 1) -> str:
    """-0.042 -> '-4.2%'."""
    if fraction is None:
        return "n/a"
    return f"{fraction * 100:+.{decimals}f}%"


def points(fraction_diff: float | None, decimals: int = 1) -> str:
    """Difference between two occupancy fractions, in percentage points: -0.02 -> '-2.0 pp'."""
    if fraction_diff is None:
        return "n/a"
    return f"{fraction_diff * 100:+.{decimals}f} pp"


def count(value: float | None) -> str:
    """13870 -> '13,870'."""
    if value is None:
        return "n/a"
    return f"{value:,.0f}"
