from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("budget", "0017_subbalance_allocation_mode_subbalanceallocation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("created_main_balance", "Created Main Balance"),
                    ("edited_main_balance", "Edited Main Balance"),
                    ("deleted_main_balance", "Deleted Main Balance"),
                    ("created_money_entry", "Added Money Entry"),
                    ("edited_money_entry", "Edited Money Entry"),
                    ("deleted_money_entry", "Deleted Money Entry"),
                    ("created_sub_balance", "Added Sub Balance"),
                    ("edited_sub_balance", "Edited Sub Balance"),
                    ("deleted_sub_balance", "Deleted Sub Balance"),
                    ("created_expense", "Recorded Expense"),
                    ("edited_expense", "Updated Expense"),
                    ("deleted_expense", "Deleted Expense"),
                ],
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="auditevent",
            name="object_type",
            field=models.CharField(
                choices=[
                    ("main_balance", "Main Balance"),
                    ("money_entry", "Money Entry"),
                    ("sub_balance", "Sub Balance"),
                    ("daily_expense", "Expense"),
                ],
                max_length=20,
            ),
        ),
    ]
