import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.faucet import get_zusd_balance


class Command(BaseCommand):
    help = "Query visible sandbox ZUSD holdings and print a safe balance summary."

    def add_arguments(self, parser):
        parser.add_argument("--owner-party", required=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        try:
            result = get_zusd_balance(owner_party=options["owner_party"])
        except ZalaryBackendError as exc:
            output = {"status": "error", "error": safe_error_message(exc)}
            self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
            raise CommandError("ZUSD balance query failed.") from exc

        self.stdout.write(json.dumps({"status": "ok", **result.safe_summary()}, indent=2, sort_keys=True))
