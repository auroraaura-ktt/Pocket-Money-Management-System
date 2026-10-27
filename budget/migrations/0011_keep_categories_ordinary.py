from django.db import migrations


def remove_automatic_category_copies(apps, schema_editor):
    """Remove only rows made by the earlier backfill; preserve categories."""
    SubBalance = apps.get_model("budget", "SubBalance")
    SubBalance.objects.filter(legacy_category__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [("budget", "0010_subbalance")]

    operations = [
        migrations.RunPython(
            remove_automatic_category_copies,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="subbalance",
            name="legacy_category",
        ),
    ]
