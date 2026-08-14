from django.db import migrations, models


PERIODS = (
    (1, "Days 1-10"),
    (2, "Days 11-20"),
    (3, "Days 21-end"),
)


def period_index_for_day(day):
    if day <= 10:
        return 1
    if day <= 20:
        return 2
    return 3


def migrate_to_period_categories(apps, schema_editor):
    BudgetMonth = apps.get_model("budget", "BudgetMonth")
    BudgetCategory = apps.get_model("budget", "BudgetCategory")
    DailyExpense = apps.get_model("budget", "DailyExpense")

    for budget_month in BudgetMonth.objects.all():
        period_categories = {}
        for period_index, name in PERIODS:
            category, _ = BudgetCategory.objects.update_or_create(
                budget_month=budget_month,
                period_index=period_index,
                defaults={"name": name, "estimated_amount": 0},
            )
            if category.name != name:
                category.name = name
                category.save(update_fields=["name"])
            period_categories[period_index] = category

        for expense in DailyExpense.objects.filter(budget_month=budget_month):
            period_index = period_index_for_day(expense.expense_date.day)
            expense.category_id = period_categories[period_index].id
            expense.save(update_fields=["category_id"])

        BudgetCategory.objects.filter(budget_month=budget_month).exclude(
            period_index__in=[1, 2, 3]
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0002_alter_budgetcategory_estimated_amount_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="budgetcategory",
            name="period_index",
            field=models.PositiveSmallIntegerField(null=True),
        ),
        migrations.RunPython(migrate_to_period_categories, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="budgetcategory",
            name="uq_month_category",
        ),
        migrations.AlterField(
            model_name="budgetcategory",
            name="period_index",
            field=models.PositiveSmallIntegerField(),
        ),
        migrations.AlterModelOptions(
            name="budgetcategory",
            options={"ordering": ["period_index"], "verbose_name_plural": "budget categories"},
        ),
        migrations.AddConstraint(
            model_name="budgetcategory",
            constraint=models.UniqueConstraint(
                fields=("budget_month", "period_index"), name="uq_month_period"
            ),
        ),
    ]
