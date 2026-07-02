import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.auth import auth_configured, ledger_api_url_configured, load_ledger_auth_settings
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.ledger import LedgerClient


class Command(BaseCommand):
    help = "Check connectivity to the configured Zalary Ledger API ledger-end endpoint."

    def handle(self, *args, **options):
        result = {
            "status": "ok",
            "ledger_api_url_configured": ledger_api_url_configured(),
            "auth_configured": auth_configured(),
            "ledger_end": None,
        }

        try:
            settings = load_ledger_auth_settings()
            result["ledger_end"] = LedgerClient(settings).get_current_ledger_offset()
        except ZalaryBackendError as exc:
            result["status"] = "error"
            result["error"] = safe_error_message(exc)
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            raise CommandError("Ledger health check failed.") from exc

        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
