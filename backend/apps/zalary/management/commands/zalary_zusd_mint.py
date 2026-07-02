import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.faucet import request_zusd_faucet_mint


class Command(BaseCommand):
    help = "Mint sandbox ZUSD to an owner party through ZUSDIssuer.MintZUSD."

    def add_arguments(self, parser):
        parser.add_argument("--owner-party", required=True)
        parser.add_argument("--amount", required=True)
        parser.add_argument("--reference", required=True)
        parser.add_argument("--request-id", default="")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        try:
            result = request_zusd_faucet_mint(
                owner_party=options["owner_party"],
                amount=options["amount"],
                reference=options["reference"],
                request_id=options["request_id"] or None,
                metadata={"source": "management_command"},
            )
        except ZalaryBackendError as exc:
            output = {"status": "error", "error": safe_error_message(exc)}
            self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
            raise CommandError("ZUSD mint failed.") from exc

        self.stdout.write(json.dumps(result.safe_summary(), indent=2, sort_keys=True))
