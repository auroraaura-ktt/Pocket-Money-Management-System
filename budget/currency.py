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
    """Format as whole MMK, e.g. '80,000 MMK'."""
    amount = int(to_mmk(value))
    return f"{amount:,} MMK"
