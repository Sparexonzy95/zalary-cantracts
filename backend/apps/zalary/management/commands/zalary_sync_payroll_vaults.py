import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import PayrollVaultMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_payroll_vaults


class Command(BaseCommand):
    help = "Sync active Zalary PayrollVault contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        payroll_id = options.get("payroll_id")
        try:
            result = sync_payroll_vaults(company_id=company_id, payroll_id=payroll_id)
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
            raise CommandError("PayrollVault sync failed.") from exc

        vaults = PayrollVaultMirror.objects.filter(contract_id__in=result.contract_ids).order_by(
            "company_id",
            "payroll_id",
            "-synced_at",
        )
        summary = {
            "status": "ok",
            "synced_count": result.synced_count,
            "company_ids": result.company_ids,
            "payroll_ids": result.payroll_ids,
            "contract_ids": result.contract_ids,
            "payroll_vaults": [
                {
                    "contract_id": vault.contract_id,
                    "company_id": vault.company_id,
                    "payroll_id": vault.payroll_id,
                    "vault_status": vault.vault_status,
                    "uploaded_allocation_count": vault.totals.get("uploadedAllocationCount"),
                    "expected_employee_count": vault.totals.get("expectedEmployeeCount"),
                }
                for vault in vaults
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
