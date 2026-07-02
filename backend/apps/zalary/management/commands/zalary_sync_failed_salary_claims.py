import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import FailedSalaryClaimMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_failed_salary_claims


class Command(BaseCommand):
    help = "Sync active Zalary FailedSalaryClaim contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")
        parser.add_argument("--employee-external-id")

    def handle(self, *args, **options):
        try:
            result = sync_failed_salary_claims(
                company_id=options.get("company_id"),
                payroll_id=options.get("payroll_id"),
                employee_external_id=options.get("employee_external_id"),
            )
        except ZalaryBackendError as exc:
            self.stdout.write(json.dumps({"status": "error", "error": safe_error_message(exc)}, indent=2, sort_keys=True))
            raise CommandError("FailedSalaryClaim sync failed.") from exc

        claims = FailedSalaryClaimMirror.objects.filter(contract_id__in=result.contract_ids).order_by("synced_at")
        summary = result.safe_summary()
        summary["status"] = "ok"
        summary["failed_salary_claims"] = [
            {
                "contract_id": claim.contract_id,
                "company_id": claim.company_id,
                "payroll_id": claim.payroll_id,
                "employee_external_id": claim.employee_external_id,
                "failure_reason": claim.failure_reason,
                "amount": str(claim.amount),
            }
            for claim in claims
        ]
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
