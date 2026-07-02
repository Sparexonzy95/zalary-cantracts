import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import PayslipMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_payslips


class Command(BaseCommand):
    help = "Sync active Zalary Payslip contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")
        parser.add_argument("--employee-external-id")

    def handle(self, *args, **options):
        try:
            result = sync_payslips(
                company_id=options.get("company_id"),
                payroll_id=options.get("payroll_id"),
                employee_external_id=options.get("employee_external_id"),
            )
        except ZalaryBackendError as exc:
            self.stdout.write(json.dumps({"status": "error", "error": safe_error_message(exc)}, indent=2, sort_keys=True))
            raise CommandError("Payslip sync failed.") from exc

        payslips = PayslipMirror.objects.filter(contract_id__in=result.contract_ids).order_by("synced_at")
        summary = result.safe_summary()
        summary["status"] = "ok"
        summary["payslips"] = [
            {
                "contract_id": payslip.contract_id,
                "company_id": payslip.company_id,
                "payroll_id": payslip.payroll_id,
                "employee_external_id": payslip.employee_external_id,
            }
            for payslip in payslips
        ]
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
