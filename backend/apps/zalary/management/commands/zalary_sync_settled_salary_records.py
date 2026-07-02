import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import SettledSalaryRecordMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_settled_salary_records


class Command(BaseCommand):
    help = "Sync active Zalary SettledSalaryRecord contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")
        parser.add_argument("--employee-external-id")

    def handle(self, *args, **options):
        try:
            result = sync_settled_salary_records(
                company_id=options.get("company_id"),
                payroll_id=options.get("payroll_id"),
                employee_external_id=options.get("employee_external_id"),
            )
        except ZalaryBackendError as exc:
            self.stdout.write(json.dumps({"status": "error", "error": safe_error_message(exc)}, indent=2, sort_keys=True))
            raise CommandError("SettledSalaryRecord sync failed.") from exc

        records = SettledSalaryRecordMirror.objects.filter(contract_id__in=result.contract_ids).order_by("synced_at")
        summary = result.safe_summary()
        summary["status"] = "ok"
        summary["settled_salary_records"] = [
            {
                "contract_id": record.contract_id,
                "company_id": record.company_id,
                "payroll_id": record.payroll_id,
                "employee_external_id": record.employee_external_id,
                "settlement_reference": record.settlement_reference,
                "amount": str(record.amount),
            }
            for record in records
        ]
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
