import json
import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.token_transfers.factory import get_token_transfer_provider
from apps.zalary.services.token_transfers.usdcx import ConfiguredUSDCxTransferProvider, InsufficientHoldingsError
from apps.zalary.services.token_transfers.base import TokenTransferRequest


class Command(BaseCommand):
    help = "Build a USDCx Token Standard transfer command plan without submitting it."

    def add_arguments(self, parser):
        parser.add_argument("--sender-party", required=True)
        parser.add_argument("--receiver-party", required=True)
        parser.add_argument("--amount", required=True)
        parser.add_argument("--instrument-id", required=True)
        parser.add_argument("--instrument-admin", required=True)
        parser.add_argument("--settlement-reference", default="")
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
        try:
            holdings = provider.list_usdcx_holdings(request)
            selected_holdings = provider.select_input_holdings(holdings, request.amount)
            discovery = provider.discover_transfer_factory(request, selected_holdings=selected_holdings)
            plan = provider.build_transfer_command_plan(
                request=request,
                selected_holdings=selected_holdings,
                discovery=discovery,
            )
        except InsufficientHoldingsError as exc:
            result = {"status": "error", "error": str(exc)}
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            raise CommandError(str(exc)) from exc
        except ZalaryBackendError as exc:
            result = {"status": "error", "error": safe_error_message(exc)}
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            raise CommandError("USDCx dry run failed.") from exc

        result = {
            "status": "ok",
            "provider": provider.safe_config(),
            "matching_holding_count": len(holdings),
            "transfer_factory": discovery.safe_summary(),
            "transfer_command_plan": plan.safe_summary(),
            "command_payload": plan.command_payload,
            "disclosed_contract_count": len(plan.disclosed_contracts),
            "argument_shape_worked": discovery.argument_shape,
            "can_submit": plan.ready,
            "can_submit_transfer": plan.ready,
        }
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))


def _request_from_options(options) -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id="dry-run",
        payroll_id="dry-run",
        employee_external_id="dry-run",
        salary_claim_contract_id="",
        token={
            "symbol": "USDCx",
            "instrumentId": options["instrument_id"],
            "instrumentAdmin": options["instrument_admin"],
        },
        sender_party=options["sender_party"],
        receiver_party=options["receiver_party"],
        amount=options["amount"],
        transfer_reference=options["settlement_reference"] or f"USDCX-DRY-RUN-{uuid.uuid4().hex}",
    )
