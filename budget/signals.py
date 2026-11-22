from decimal import Decimal

from django.db.models import Sum
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from budget.models import BudgetMonth, InputBalance
from budget.services.category_service import create_default_period_categories


@receiver(post_save, sender=BudgetMonth)
def create_period_categories(sender, instance, created, **kwargs):
    if created:
        create_default_period_categories(instance)
        InputBalance.objects.create(
            budget_month=instance,
            category=instance.category,
            amount=instance.total_money,
        )


@receiver([post_save, post_delete], sender=InputBalance)
def sync_legacy_month_total(sender, instance, **kwargs):
    total = InputBalance.objects.filter(
        budget_month_id=instance.budget_month_id
    ).aggregate(total=Sum("amount"))["total"]
    BudgetMonth.objects.filter(pk=instance.budget_month_id).update(
        total_money=total if total is not None else Decimal("0")
    )
