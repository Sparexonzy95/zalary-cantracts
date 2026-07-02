import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import EmployeeEnrollmentMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_employee_enrollments


class Command(BaseCommand):
    help = "Sync active Zalary EmployeeEnrollment contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        try:
            result = sync_employee_enrollments(company_id=company_id)
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": company_id or "",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("EmployeeEnrollment sync failed.") from exc

        enrollments = EmployeeEnrollmentMirror.objects.filter(contract_id__in=result.contract_ids).order_by(
            "company_id",
            "employee_external_id",
        )
        summary = {
            "status": "ok",
            "synced_count": result.synced_count,
            "company_ids": result.company_ids,
            "employee_external_ids": result.employee_external_ids,
            "contract_ids": result.contract_ids,
            "enrollments": [
                {
                    "contract_id": enrollment.contract_id,
                    "company_id": enrollment.company_id,
                    "employee_external_id": enrollment.employee_external_id,
                    "is_active": enrollment.is_active,
                }
                for enrollment in enrollments
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
