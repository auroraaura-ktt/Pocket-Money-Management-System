from django.db.models.signals import post_save
from django.dispatch import receiver

from budget.models import BudgetMonth
from budget.services.category_service import create_default_period_categories


@receiver(post_save, sender=BudgetMonth)
def create_period_categories(sender, instance, created, **kwargs):
    if created:
        create_default_period_categories(instance)
