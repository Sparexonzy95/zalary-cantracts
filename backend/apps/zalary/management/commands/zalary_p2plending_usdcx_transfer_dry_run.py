import json
import os

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.token_transfers.base import TokenTransferRequest
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
    help = "Build a fail-closed P2PLending USDCx transfer plan without submitting it."

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
        try:
            plan = provider.build_p2plending_transfer_plan(request)
        except ZalaryBackendError as exc:
            result = {
                "status": "error",
                "can_submit": False,
                "blockers": [safe_error_message(exc)],
                "zalary_settlement_confirmed": False,
            }
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            raise CommandError("P2PLending USDCx dry-run failed.") from exc

        summary = plan.safe_summary()
        result = {
            "status": "ok",
            "provider_mode": P2PLENDING_PROVIDER_MODE,
            "selected_holding_cid_truncated": summary["selected_holding_cid"],
            "selected_holding_amount": summary["selected_holding_amount"],
            "exact_amount_holding_available": summary["exact_amount_holding_available"],
            "split_required": summary["split_required"],
            "registry_contract_found": summary["registry_contract_found"],
            "registry_contract_id_truncated": summary["registry_contract_id"],
            "registry_contract_template": summary["registry_contract_template"],
            "transfer_choice_selected": summary["transfer_choice_selected"],
            "split_choice_selected": summary["split_choice_selected"],
            "act_as": summary["act_as"],
            "transfer_choice_argument_shape": summary["transfer_argument_shape"],
            "split_choice_argument_shape": summary["split_argument_shape"],
            "schema": summary["schema"],
            "can_submit": summary["can_submit"],
            "blockers": summary["blockers"],
            "zalary_settlement_confirmed": False,
        }
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
        if not summary["can_submit"]:
            raise CommandError("P2PLending USDCx dry-run is not submittable.")


def _provider() -> ConfiguredUSDCxTransferProvider:
    return ConfiguredUSDCxTransferProvider(
        provider_mode=P2PLENDING_PROVIDER_MODE,
        timeout_seconds=_timeout_seconds(),
        holding_interface_id=os.environ.get(USDCX_HOLDING_INTERFACE_ID, "").strip() or DEFAULT_HOLDING_INTERFACE_ID,
    )


def _request_from_options(options) -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id="p2plending-dry-run",
        payroll_id="p2plending-dry-run",
        employee_external_id="p2plending-dry-run",
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
