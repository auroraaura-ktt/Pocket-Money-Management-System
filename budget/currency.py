"""MMK currency helpers — whole kyat only, no float math."""

from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal("0")
ONE = Decimal("1")


def to_mmk(value) -> Decimal:
    """Round any amount to a whole MMK value."""
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(ONE, rounding=ROUND_HALF_UP)


def format_mmk(value) -> str:
    """Format as whole MMK, e.g. '80,000 MMK'.

    The absolute value is always shown so no negative money amount is ever
    displayed. Negative values are only produced for deficits or unexpected
    income; they remain signed internally so all calculations stay correct,
    and callers can colour the row based on the underlying sign.
    """
    amount = abs(int(to_mmk(value)))
    return f"{amount:,} MMK"
