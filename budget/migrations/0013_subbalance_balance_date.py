from datetime import date

from django.db import migrations, models


def populate_sub_balance_dates(apps, schema_editor):
    SubBalance = apps.get_model("budget", "SubBalance")
    for sub_balance in SubBalance.objects.select_related("budget_month").iterator():
        parent = sub_balance.budget_month
        SubBalance.objects.filter(pk=sub_balance.pk).update(
            balance_date=date(parent.year, parent.month, 1)
        )


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0012_budgetcategory_sub_balance_dailyexpense_sub_balance_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="subbalance",
            name="balance_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(
            populate_sub_balance_dates,
            migrations.RunPython.noop,
        ),
    ]
