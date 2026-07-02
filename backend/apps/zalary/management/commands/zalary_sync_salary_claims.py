import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import SalaryClaimMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_salary_claims


class Command(BaseCommand):
    help = "Sync active Zalary SalaryClaim contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")
        parser.add_argument("--employee-external-id")

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        payroll_id = options.get("payroll_id")
        employee_external_id = options.get("employee_external_id")
        try:
            result = sync_salary_claims(
                company_id=company_id,
                payroll_id=payroll_id,
                employee_external_id=employee_external_id,
            )
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": company_id or "",
                        "payroll_id": payroll_id or "",
                        "employee_external_id": employee_external_id or "",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("SalaryClaim sync failed.") from exc

        claims = SalaryClaimMirror.objects.filter(contract_id__in=result.contract_ids).order_by(
            "company_id",
            "payroll_id",
            "employee_external_id",
        )
        summary = {
            "status": "ok",
            "synced_count": result.synced_count,
            "company_ids": result.company_ids,
            "payroll_ids": result.payroll_ids,
            "employee_external_ids": result.employee_external_ids,
            "contract_ids": result.contract_ids,
            "salary_claims": [
                {
                    "contract_id": claim.contract_id,
                    "company_id": claim.company_id,
                    "payroll_id": claim.payroll_id,
                    "employee_external_id": claim.employee_external_id,
                    "claim_status": claim.claim_status,
                    "claim_amount": str(claim.claim_amount),
                    "source_allocation_contract_id": claim.source_allocation_contract_id,
                }
                for claim in claims
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
