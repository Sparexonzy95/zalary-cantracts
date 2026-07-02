import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import SettlementProofError
from apps.zalary.services.token_transfers.factory import get_token_transfer_provider
from apps.zalary.services.token_transfers.usdcx import ConfiguredUSDCxTransferProvider
from apps.zalary.services.token_transfers.base import (
    TokenTransferRequest,
    TRANSFER_COMPLETED,
    TRANSFER_PENDING,
    TRANSFER_PENDING_RECEIVER_ACCEPTANCE,
)


class Command(BaseCommand):
    help = "Submit a real USDCx Token Standard transfer only; this does not confirm Zalary settlement."

    def add_arguments(self, parser):
        parser.add_argument("--sender-party", required=True)
        parser.add_argument("--receiver-party", required=True)
        parser.add_argument("--amount", required=True)
        parser.add_argument("--instrument-id", required=True)
        parser.add_argument("--instrument-admin", required=True)
        parser.add_argument("--settlement-reference", default="")
        parser.add_argument("--allow-pending", action="store_true")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        provider = get_token_transfer_provider()
        if not isinstance(provider, ConfiguredUSDCxTransferProvider):
            result = {
                "status": "error",
                "blockers": [
                    "USDCx provider is not configured. Set ZALARY_TOKEN_TRANSFER_PROVIDER=usdcx and "
                    "ZALARY_USDCX_TRANSFER_PROVIDER=token_standard."
                ],
            }
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            raise CommandError("USDCx provider is not configured.")

        request = _request_from_options(options)
        result = provider.execute_transfer(request)
        output = {
            "status": result.status,
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
                raise CommandError("USDCx transfer completed but proof construction failed.") from exc

        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
        if result.status in {TRANSFER_PENDING, TRANSFER_PENDING_RECEIVER_ACCEPTANCE} and options["allow_pending"]:
            return
        if result.status != TRANSFER_COMPLETED:
            raise CommandError(result.error_message or f"USDCx transfer returned {result.status}.")


def _request_from_options(options) -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id="direct-transfer",
        payroll_id="direct-transfer",
        employee_external_id="direct-transfer",
        salary_claim_contract_id="",
        token={
            "symbol": "USDCx",
            "instrumentId": options["instrument_id"],
            "instrumentAdmin": options["instrument_admin"],
        },
        sender_party=options["sender_party"],
        receiver_party=options["receiver_party"],
        amount=options["amount"],
        transfer_reference=options["settlement_reference"] or f"USDCX-TRANSFER-{uuid.uuid4().hex}",
        metadata={"allow_pending": bool(options["allow_pending"])},
    )
