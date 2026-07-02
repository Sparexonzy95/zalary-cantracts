import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import SettlementReceiptMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_settlement_receipts


class Command(BaseCommand):
    help = "Sync active Zalary SettlementReceipt contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")
        parser.add_argument("--employee-external-id")

    def handle(self, *args, **options):
        try:
            result = sync_settlement_receipts(
                company_id=options.get("company_id"),
                payroll_id=options.get("payroll_id"),
                employee_external_id=options.get("employee_external_id"),
            )
        except ZalaryBackendError as exc:
            self.stdout.write(json.dumps({"status": "error", "error": safe_error_message(exc)}, indent=2, sort_keys=True))
            raise CommandError("SettlementReceipt sync failed.") from exc

        receipts = SettlementReceiptMirror.objects.filter(contract_id__in=result.contract_ids).order_by("synced_at")
        summary = result.safe_summary()
        summary["status"] = "ok"
        summary["settlement_receipts"] = [
            {
                "contract_id": receipt.contract_id,
                "company_id": receipt.company_id,
                "payroll_id": receipt.payroll_id,
                "employee_external_id": receipt.employee_external_id,
                "settlement_reference": receipt.settlement_reference,
                "amount": str(receipt.amount),
            }
            for receipt in receipts
        ]
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
