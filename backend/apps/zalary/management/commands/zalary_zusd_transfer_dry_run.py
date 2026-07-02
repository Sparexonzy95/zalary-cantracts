import json
import os

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.token_transfers.base import TokenTransferRequest
from apps.zalary.services.token_transfers.zusd import (
    ZALARY_TEST_TOKEN_ISSUER_PARTY,
    ConfiguredZUSDTransferProvider,
    zusd_token_instrument,
)


class Command(BaseCommand):
    help = "Build a sandbox ZUSD transfer plan without submitting it."

    def add_arguments(self, parser):
        parser.add_argument("--sender-party", required=True)
        parser.add_argument("--receiver-party", required=True)
        parser.add_argument("--amount", required=True)
        parser.add_argument("--settlement-reference", required=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        provider = ConfiguredZUSDTransferProvider()
        request = _request_from_options(options)
        plan = provider.build_transfer_plan(request)
        output = {
            "status": "ok" if plan.ready else "error",
            "provider": provider.provider_name,
            "transfer_plan": plan.safe_summary(),
            "can_submit": plan.ready,
            "zalary_settlement_confirmed": False,
        }
        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
        if not plan.ready:
            raise CommandError("ZUSD transfer dry-run is not submittable.")


def _request_from_options(options) -> TokenTransferRequest:
    issuer_party = os.environ.get(ZALARY_TEST_TOKEN_ISSUER_PARTY, "").strip() or options["sender_party"]
    return TokenTransferRequest(
        company_id="zusd-dry-run",
        payroll_id="zusd-dry-run",
        employee_external_id="zusd-dry-run",
        salary_claim_contract_id="",
        token=zusd_token_instrument(issuer_party=issuer_party),
        sender_party=options["sender_party"],
        receiver_party=options["receiver_party"],
        amount=options["amount"],
        transfer_reference=options["settlement_reference"],
    )
