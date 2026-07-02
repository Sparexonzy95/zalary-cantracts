import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import SalaryAllocationMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_salary_allocations


class Command(BaseCommand):
    help = "Sync active Zalary SalaryAllocation contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        payroll_id = options.get("payroll_id")
        try:
            result = sync_salary_allocations(company_id=company_id, payroll_id=payroll_id)
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": company_id or "",
                        "payroll_id": payroll_id or "",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("SalaryAllocation sync failed.") from exc

        allocations = SalaryAllocationMirror.objects.filter(contract_id__in=result.contract_ids).order_by(
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
            "salary_allocations": [
                {
                    "contract_id": allocation.contract_id,
                    "company_id": allocation.company_id,
                    "payroll_id": allocation.payroll_id,
                    "employee_external_id": allocation.employee_external_id,
                    "allocation_status": allocation.allocation_status,
                }
                for allocation in allocations
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
