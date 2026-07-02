import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import LedgerContract
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_funding_receipts


class Command(BaseCommand):
    help = "Sync active Zalary FundingReceipt contracts into LedgerContract storage."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        payroll_id = options.get("payroll_id")
        try:
            result = sync_funding_receipts(company_id=company_id, payroll_id=payroll_id)
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
            raise CommandError("FundingReceipt sync failed.") from exc

        receipts = LedgerContract.objects.filter(contract_id__in=result.contract_ids).order_by("contract_id")
        summary = {
            "status": "ok",
            "synced_count": result.synced_count,
            "company_ids": result.company_ids,
            "payroll_ids": result.payroll_ids,
            "contract_ids": result.contract_ids,
            "funding_receipts": [
                {
                    "contract_id": receipt.contract_id,
                    "company_id": (receipt.payload or {}).get("fundingCompanyId") or "",
                    "payroll_id": (receipt.payload or {}).get("fundingPayrollId") or "",
                    "funding_amount": (receipt.payload or {}).get("fundingAmount") or "",
                    "funding_reference": (receipt.payload or {}).get("fundingReference") or "",
                }
                for receipt in receipts
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
