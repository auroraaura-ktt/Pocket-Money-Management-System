from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0004_reportemaillog"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="budgetcategory",
            name="uq_month_period",
        ),
        migrations.AlterField(
            model_name="budgetcategory",
            name="period_index",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AlterModelOptions(
            name="budgetcategory",
            options={
                "ordering": ["period_index", "id"],
                "verbose_name_plural": "budget categories",
            },
        ),
        migrations.AddConstraint(
            model_name="budgetcategory",
            constraint=models.UniqueConstraint(
                fields=("budget_month", "name"), name="uq_month_category_name"
            ),
        ),
    ]