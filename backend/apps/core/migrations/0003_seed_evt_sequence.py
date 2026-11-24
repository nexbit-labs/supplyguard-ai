from django.db import migrations


def create_evt_sequence(apps, schema_editor):
    """Ids of simulated events (EVT-900001, ...) start above the seed/scenario ids (EVT-0009xx)."""
    IdSequence = apps.get_model("core", "IdSequence")
    IdSequence.objects.get_or_create(prefix="EVT", defaults={"last_value": 900000})


class Migration(migrations.Migration):
    dependencies = [("core", "0002_seed_id_sequences")]

    operations = [migrations.RunPython(create_evt_sequence, migrations.RunPython.noop)]
