from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0014_inputbalance"),
    ]

    operations = [
        migrations.AddField(
            model_name="budgetmonth",
            name="category",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.RemoveConstraint(
            model_name="budgetmonth",
            name="uq_user_month",
        ),
    ]
