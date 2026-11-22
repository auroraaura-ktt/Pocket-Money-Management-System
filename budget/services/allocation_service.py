"""Money-entry allocation calculations."""

from decimal import Decimal

from budget.models import SubBalance


def allocated_amount_for_entry(input_balance, exclude_sub_balance_id=None):
    allocations = SubBalance.objects.filter(source_input_balance=input_balance)
    if exclude_sub_balance_id is not None:
        allocations = allocations.exclude(pk=exclude_sub_balance_id)

    total = Decimal("0")
    for allocation in allocations.exclude(
        allocation_mode=SubBalance.INHERIT_MAIN_TOTAL
    ):
        total += allocation.allocated_amount or Decimal("0")
    return total


def available_amount_for_entry(input_balance, exclude_sub_balance_id=None):
    return input_balance.amount - allocated_amount_for_entry(
        input_balance, exclude_sub_balance_id
    )
