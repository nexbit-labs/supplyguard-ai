from django.db import models, transaction


class IdSequence(models.Model):
    """Global counter behind the system-generated ids RC-, REC-, APR-, EXE- (CONTRACTS s10).

    Rows are created by a data migration. Ids are not contiguous per tenant (accepted for v1).
    """

    prefix = models.CharField(primary_key=True, max_length=10)
    last_value = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        return f"{self.prefix}: {self.last_value}"


def next_id(prefix: str, width: int = 6) -> str:
    """Return the next id for `prefix`, e.g. `next_id("RC", 6) -> "RC-000045"`.

    The row is locked with select_for_update inside transaction.atomic(); on SQLite the lock is
    a no-op, so concurrency is only covered on PostgreSQL.
    """
    with transaction.atomic():
        seq = IdSequence.objects.select_for_update().get(prefix=prefix)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
        return f"{prefix}-{seq.last_value:0{width}d}"
