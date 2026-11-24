"""Re-run start_execution for APPROVED cases whose Execution is still PENDING (lost on_commit,
WORKFLOW_DESIGN.md s4.6)."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import Execution, RiskCase
from apps.workflow.services import start_execution


class Command(BaseCommand):
    help = "Resume PENDING executions of APPROVED cases older than --min-age seconds."

    def add_arguments(self, parser):
        parser.add_argument("--min-age", type=int, default=60)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(seconds=options["min_age"])
        stuck = Execution.objects.filter(
            status=Execution.Status.PENDING,
            approval__risk_case__status=RiskCase.Status.APPROVED,
            created_at__lte=cutoff,
        ).select_related("approval__risk_case")
        count = 0
        for exe in stuck:
            start_execution(exe.pk, exe.tenant_id, exe.approval.risk_case.correlation_id)
            count += 1
        self.stdout.write(f"resumed {count} execution(s)")
