import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.auth import auth_configured, ledger_api_url_configured
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.token_transfers.factory import get_token_transfer_provider
from apps.zalary.services.token_transfers.usdcx import ConfiguredUSDCxTransferProvider


class Command(BaseCommand):
    help = "Safely diagnose USDCx Token Standard holdings and TransferFactory discovery without submitting a transfer."

    def add_arguments(self, parser):
        parser.add_argument("--party", required=True, help="Employer/sender Party ID used to read USDCx holdings.")
        parser.add_argument("--instrument-id", required=True, help="USDCx instrument id.")
        parser.add_argument("--instrument-admin", required=True, help="USDCx instrument admin Party ID.")
        parser.add_argument("--max-holdings", type=int, default=10)
        parser.add_argument("--include-contract-keys", action="store_true", help="Accepted for compatibility; full keys are never printed.")
        parser.add_argument("--json", action="store_true", help="Print JSON output.")

    def handle(self, *args, **options):
        provider = get_token_transfer_provider()
        if not isinstance(provider, ConfiguredUSDCxTransferProvider):
            result = {
                "status": "error",
                "ledger_api_url_configured": ledger_api_url_configured(),
                "auth_configured": auth_configured(),
                "blockers": [
                    "USDCx provider is not configured. Set ZALARY_TOKEN_TRANSFER_PROVIDER=usdcx and "
                    "ZALARY_USDCX_TRANSFER_PROVIDER=token_standard."
                ],
            }
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            raise CommandError("USDCx provider is not configured.")

        request = _request_from_options(options, receiver_party=options["party"], amount="1.0000000000")
        try:
            summary = provider.diagnostic_summary(
                request,
                max_holdings=max(options["max_holdings"], 0),
            )
        except ZalaryBackendError as exc:
            summary = {"status": "error", "error": safe_error_message(exc)}
            self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
            raise CommandError("USDCx discovery failed.") from exc

        result = {
            "status": "ok",
            "ledger_api_url_configured": ledger_api_url_configured(),
            "auth_configured": auth_configured(),
            "selected_party": options["party"],
            "instrument_id": options["instrument_id"],
            "instrument_admin": options["instrument_admin"],
            **summary,
        }
        transfer_factory = summary.get("transfer_factory") or {}
        result.update(
            {
                "factoryId_present": bool(transfer_factory.get("factoryId_present")),
                "transferKind": transfer_factory.get("transferKind") or "",
                "choiceContext_present": bool(transfer_factory.get("choiceContext_present")),
                "choiceContextData_present": bool(transfer_factory.get("choiceContextData_present")),
                "disclosedContracts_count": transfer_factory.get("disclosedContracts_count") or 0,
                "can_build_final_choice_argument": bool(
                    transfer_factory.get("can_build_final_choice_argument")
                ),
                "can_submit_live_transfer": bool(transfer_factory.get("can_submit_live_transfer")),
                "argument_shape_worked": transfer_factory.get("argument_shape") or "",
            }
        )
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))


def _request_from_options(options, *, receiver_party: str, amount: str):
    from apps.zalary.services.token_transfers.base import TokenTransferRequest

    return TokenTransferRequest(
        company_id="schema-discovery",
        payroll_id="schema-discovery",
        employee_external_id="schema-discovery",
        salary_claim_contract_id="",
        token={
            "symbol": "USDCx",
            "instrumentId": options["instrument_id"],
            "instrumentAdmin": options["instrument_admin"],
        },
        sender_party=options["party"],
        receiver_party=receiver_party,
        amount=amount,
        transfer_reference="schema-discovery",
    )
