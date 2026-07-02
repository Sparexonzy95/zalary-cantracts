import json
import os

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import SettlementProofError
from apps.zalary.services.token_transfers.base import TRANSFER_COMPLETED, TokenTransferRequest
from apps.zalary.services.token_transfers.zusd import (
    ZALARY_TEST_TOKEN_ISSUER_PARTY,
    ConfiguredZUSDTransferProvider,
    zusd_token_instrument,
)


class Command(BaseCommand):
    help = "Submit a sandbox ZUSD token-only transfer; this does not confirm Zalary payroll settlement."

    def add_arguments(self, parser):
        parser.add_argument("--sender-party", required=True)
        parser.add_argument("--receiver-party", required=True)
        parser.add_argument("--amount", required=True)
        parser.add_argument("--settlement-reference", required=True)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        provider = ConfiguredZUSDTransferProvider()
        request = _request_from_options(options)
        result = provider.execute_transfer(request)
        output = {
            "status": result.status,
            "provider": provider.provider_name,
            "transfer": result.safe_summary(),
            "proof": None,
            "zalary_settlement_confirmed": False,
        }
        if result.status == TRANSFER_COMPLETED:
            try:
                output["proof"] = provider.build_token_transfer_proof(result)
            except SettlementProofError as exc:
                output["status"] = "error"
                output["error"] = str(exc)
                self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
                raise CommandError("ZUSD transfer completed but proof construction failed.") from exc

        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
        if result.status != TRANSFER_COMPLETED:
            raise CommandError(result.error_message or f"ZUSD transfer returned {result.status}.")


def _request_from_options(options) -> TokenTransferRequest:
    issuer_party = os.environ.get(ZALARY_TEST_TOKEN_ISSUER_PARTY, "").strip() or options["sender_party"]
    return TokenTransferRequest(
        company_id="zusd-live-transfer",
        payroll_id="zusd-live-transfer",
        employee_external_id="zusd-live-transfer",
        salary_claim_contract_id="",
        token=zusd_token_instrument(issuer_party=issuer_party),
        sender_party=options["sender_party"],
        receiver_party=options["receiver_party"],
        amount=options["amount"],
        transfer_reference=options["settlement_reference"],
    )
