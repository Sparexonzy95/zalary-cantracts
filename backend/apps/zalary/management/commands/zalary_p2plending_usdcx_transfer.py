import json
import os

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import SettlementProofError
from apps.zalary.services.token_transfers.base import (
    TRANSFER_COMPLETED,
    TokenTransferRequest,
)
from apps.zalary.services.token_transfers.factory import (
    USDCX_HOLDING_INTERFACE_ID,
    USDCX_TRANSFER_TIMEOUT_SECONDS,
)
from apps.zalary.services.token_transfers.usdcx import (
    DEFAULT_HOLDING_INTERFACE_ID,
    DEFAULT_USDCX_INSTRUMENT_ADMIN,
    P2PLENDING_PROVIDER_MODE,
    ConfiguredUSDCxTransferProvider,
)


class Command(BaseCommand):
    help = "Submit a token-only P2PLending USDCx transfer; this does not confirm Zalary settlement."

    def add_arguments(self, parser):
        parser.add_argument("--sender-party", required=True)
        parser.add_argument("--receiver-party", required=True)
        parser.add_argument("--amount", required=True)
        parser.add_argument("--settlement-reference", required=True)
        parser.add_argument("--instrument-admin", default=DEFAULT_USDCX_INSTRUMENT_ADMIN)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        provider = _provider()
        request = _request_from_options(options)
        result = provider.execute_transfer(request)
        output = {
            "status": result.status,
            "provider_mode": P2PLENDING_PROVIDER_MODE,
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
                raise CommandError("P2PLending USDCx transfer completed but proof construction failed.") from exc

        self.stdout.write(json.dumps(output, indent=2, sort_keys=True))
        if result.status != TRANSFER_COMPLETED:
            raise CommandError(result.error_message or f"P2PLending USDCx transfer returned {result.status}.")


def _provider() -> ConfiguredUSDCxTransferProvider:
    return ConfiguredUSDCxTransferProvider(
        provider_mode=P2PLENDING_PROVIDER_MODE,
        timeout_seconds=_timeout_seconds(),
        holding_interface_id=os.environ.get(USDCX_HOLDING_INTERFACE_ID, "").strip() or DEFAULT_HOLDING_INTERFACE_ID,
    )


def _request_from_options(options) -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id="p2plending-live-transfer",
        payroll_id="p2plending-live-transfer",
        employee_external_id="p2plending-live-transfer",
        salary_claim_contract_id="",
        token={
            "symbol": "USDCx",
            "instrumentId": "USDCx",
            "instrumentAdmin": options["instrument_admin"],
        },
        sender_party=options["sender_party"],
        receiver_party=options["receiver_party"],
        amount=options["amount"],
        transfer_reference=options["settlement_reference"],
    )


def _timeout_seconds() -> int:
    try:
        return max(int(os.environ.get(USDCX_TRANSFER_TIMEOUT_SECONDS, "60")), 1)
    except ValueError:
        return 60
