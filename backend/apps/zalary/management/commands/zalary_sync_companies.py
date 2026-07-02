import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import CompanyMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_companies


class Command(BaseCommand):
    help = "Sync active Zalary Company contracts from the configured Ledger API party."

    def handle(self, *args, **options):
        try:
            result = sync_companies()
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("Company sync failed.") from exc

        companies = CompanyMirror.objects.filter(contract_id__in=result.contract_ids).order_by("company_id")
        summary = {
            "status": "ok",
            "synced_count": result.synced_count,
            "company_ids": result.company_ids,
            "contract_ids": result.contract_ids,
            "companies": [
                {
                    "contract_id": company.contract_id,
                    "company_id": company.company_id,
                    "company_name": company.company_name,
                }
                for company in companies
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
