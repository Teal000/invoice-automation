"""Decimal money handling.

All monetary values in this project are :class:`decimal.Decimal`. Floats are
never used for amounts. The configured currency is TND, which is subdivided
into 1000 millimes, so amounts carry three decimal places by default.

Every quantisation in the system goes through :func:`quantize_money` so that
the precision and rounding mode are applied in exactly one place and can be
stated in the documentation without guessing.
"""

from __future__ import annotations

import decimal
from decimal import Decimal, InvalidOperation

#: Rounding modes that may be named in configuration.
ALLOWED_ROUNDING_MODES = (
    decimal.ROUND_HALF_UP,
    decimal.ROUND_HALF_EVEN,
    decimal.ROUND_DOWN,
    decimal.ROUND_UP,
)


class MoneyParseError(ValueError):
    """Raised when a raw value cannot be read as a decimal amount."""


def exponent(decimal_places: int) -> Decimal:
    """Return the quantisation exponent for ``decimal_places`` (e.g. 0.001)."""
    if decimal_places < 0:
        raise ValueError("decimal_places must not be negative")
    return Decimal(1).scaleb(-decimal_places)


def quantize_money(
    value: Decimal,
    decimal_places: int,
    rounding: str = decimal.ROUND_HALF_UP,
) -> Decimal:
    """Quantise ``value`` to ``decimal_places`` using ``rounding``."""
    if rounding not in ALLOWED_ROUNDING_MODES:
        raise ValueError(f"unsupported rounding mode: {rounding!r}")
    return value.quantize(exponent(decimal_places), rounding=rounding)


def parse_decimal(raw: str | int | Decimal | None) -> Decimal:
    """Parse a raw extracted value into a :class:`Decimal`.

    Accepts the forms the synthetic documents use: plain decimal points,
    thousands separators written as spaces or non-breaking spaces, a comma as
    the decimal separator (common on French-labelled documents), and a
    trailing or leading currency word. Anything else raises
    :class:`MoneyParseError` rather than being guessed at, so the caller can
    record the field as unparseable instead of inventing a number.
    """
    if raw is None:
        raise MoneyParseError("value is missing")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)

    text = str(raw).strip()
    if not text:
        raise MoneyParseError("value is empty")

    # Strip currency words and spacing used as a thousands separator.
    for token in ("TND", "DT", "dinars", "dinar", " ", " ", " "):
        text = text.replace(token, "")

    # A comma is treated as the decimal separator only when no dot is present;
    # "1,234.500" keeps the dot and drops the comma as a group separator.
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - exercised via tests
        raise MoneyParseError(f"cannot read {raw!r} as a decimal amount") from exc


def within_tolerance(expected: Decimal, observed: Decimal, tolerance: Decimal) -> bool:
    """True when ``observed`` differs from ``expected`` by at most ``tolerance``."""
    return abs(observed - expected) <= tolerance
