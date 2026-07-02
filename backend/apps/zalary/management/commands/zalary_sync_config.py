import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import ZalaryConfigMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_zalary_config


class Command(BaseCommand):
    help = "Sync active ZalaryConfig contracts from the configured Ledger API party."

    def handle(self, *args, **options):
        try:
            result = sync_zalary_config()
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
            raise CommandError("ZalaryConfig sync failed.") from exc

        configs = ZalaryConfigMirror.objects.filter(contract_id__in=result.contract_ids).order_by("contract_id")
        summary = {
            "status": "ok",
            "synced_count": result.synced_count,
            "marked_inactive_count": result.marked_inactive_count,
            "configs": [
                {
                    "contract_id": config.contract_id,
                    "is_active": config.is_active,
                    "ledger_active": config.ledger_active,
                }
                for config in configs
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
