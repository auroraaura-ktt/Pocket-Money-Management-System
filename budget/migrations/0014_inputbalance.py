from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def migrate_monthly_amounts(apps, schema_editor):
    BudgetMonth = apps.get_model("budget", "BudgetMonth")
    InputBalance = apps.get_model("budget", "InputBalance")
    database = schema_editor.connection.alias
    for month in BudgetMonth.objects.using(database).iterator():
        InputBalance.objects.using(database).create(
            budget_month_id=month.pk,
            category="",
            amount=month.total_money or Decimal("0"),
            note="",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0013_subbalance_balance_date"),
    ]

    operations = [
        migrations.CreateModel(
            name="InputBalance",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("category", models.CharField(blank=True, default="", max_length=100)),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=0,
                        max_digits=12,
                        validators=[django.core.validators.MinValueValidator(0)],
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "budget_month",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="input_balances",
                        to="budget.budgetmonth",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.RunPython(
            migrate_monthly_amounts,
            migrations.RunPython.noop,
        ),
    ]
