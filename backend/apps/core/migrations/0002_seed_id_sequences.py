from django.db import migrations

PREFIXES = ["RC", "REC", "APR", "EXE"]


def create_sequences(apps, schema_editor):
    IdSequence = apps.get_model("core", "IdSequence")
    for prefix in PREFIXES:
        IdSequence.objects.get_or_create(prefix=prefix, defaults={"last_value": 0})


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(create_sequences, migrations.RunPython.noop)]
