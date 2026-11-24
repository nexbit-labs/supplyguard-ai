import logging

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.apikeys import generate_key, hash_key, key_prefix
from apps.accounts.models import ApiKey
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Create an API key for a tenant's SYSTEM caller (Boomi). The key is printed once."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Tenant id, e.g. TEN-ACME")
        parser.add_argument("--label", required=True, help="Caller label, e.g. boomi")

    def handle(self, *args, tenant, label, **options):
        try:
            tenant_obj = Tenant.objects.get(pk=tenant)
        except Tenant.DoesNotExist:
            raise CommandError(f"Tenant {tenant!r} does not exist.") from None
        if tenant_obj.status != Tenant.Status.ACTIVE:
            raise CommandError(f"Tenant {tenant!r} is not ACTIVE.")
        raw = generate_key()
        key = ApiKey.objects.create(
            tenant=tenant_obj, label=label, prefix=key_prefix(raw), key_hash=hash_key(raw)
        )
        try:
            from apps.audit import record

            record(
                "SYSTEM",
                "api_key.created",
                key,
                tenant=tenant_obj,
                data={"label": label, "prefix": key.prefix},
            )
        except Exception:
            logger.exception("audit write failed for api_key.created")
        self.stdout.write(f"API key created for {tenant_obj.id} (label {label!r}, id {key.pk}).")
        self.stdout.write("This key is shown ONCE. Store it in the Boomi environment extension;")
        self.stdout.write("it cannot be shown again (only its hash is kept).")
        self.stdout.write(raw)
