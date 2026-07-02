import json
import os

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.auth import (
    DAML_PACKAGE_NAME,
    PLATFORM_CONFIG_CONTRACT_ID,
    auth_configured,
    default_read_parties,
    ledger_api_url_configured,
    load_ledger_auth_settings,
)
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.ledger import LedgerClient
from apps.zalary.services.templates import DEFAULT_PACKAGE_ID, DEFAULT_PACKAGE_NAME


def _truncate_contract_id(contract_id: str) -> str:
    if len(contract_id) <= 24:
        return contract_id
    return f"{contract_id[:12]}...{contract_id[-8:]}"


def _template_contains(contracts: list[dict], text: str) -> bool:
    needle = text.lower()
    for contract in contracts:
        template_id = str(contract.get("template_id") or "").lower()
        package_name = str(contract.get("package_name") or "").lower()
        if needle in template_id or needle in package_name:
            return True
    return False


class Command(BaseCommand):
    help = "Safely diagnose active-contract visibility for ZalaryConfig reads."

    def handle(self, *args, **options):
        package_name = os.environ.get(DAML_PACKAGE_NAME) or DEFAULT_PACKAGE_NAME
        read_parties = default_read_parties()
        configured_contract_id = os.environ.get(PLATFORM_CONFIG_CONTRACT_ID, "").strip()

        summary = {
            "status": "ok",
            "ledger_api_url_configured": ledger_api_url_configured(),
            "auth_configured": auth_configured(),
            "selected_ledger_party": read_parties[0] if read_parties else "",
            "read_parties": read_parties,
            "package_name": package_name,
            "configured_platform_config_contract_id": configured_contract_id,
            "ledger_end": None,
            "active_at_offset_used": None,
            "active_contract_endpoint_call_succeeded": False,
            "wildcard_response_shape": None,
            "template_response_shape": None,
            "query_shape_variants": [],
            "best_query_shape_contract_count": 0,
            "successful_query_shapes": [],
            "wildcard_real_filter_count": 0,
            "template_exact_package_id_filter_count": 0,
            "total_number_active_contracts_returned_by_wildcard_query": 0,
            "template_filtered_contract_count": 0,
            "unique_template_ids_seen": [],
            "first_10_contract_ids_truncated": [],
            "first_10_contract_summaries": [],
            "configured_zalary_config_contract_id_found_in_wildcard_results": False,
            "configured_zalary_config_contract_id_found": False,
            "configured_contract_event_query": None,
            "configured_contract_event_query_found": False,
            "configured_contract_event_archived_present": False,
            "template_id_contains": {
                DEFAULT_PACKAGE_ID: False,
                "Zalary.Platform": False,
                "ZalaryConfig": False,
                "zalary-usdcx-contracts": False,
            },
            "zalary_config_like_contract_count": 0,
        }

        try:
            settings = load_ledger_auth_settings()
            client = LedgerClient(settings)
            summary["ledger_end"] = client.get_current_ledger_offset()
            diagnostics = client.diagnose_active_contracts(parties=read_parties)
        except ZalaryBackendError as exc:
            summary["status"] = "error"
            summary["error"] = safe_error_message(exc)
            self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
            raise CommandError("Active-contract diagnostics failed.") from exc

        contracts = diagnostics["wildcard_contracts"]
        template_contracts = diagnostics.get("template_contracts", [])
        configured_contract_event_query = diagnostics.get("configured_contract_event_query") or {}
        configured_event_contract = configured_contract_event_query.get("contract")
        event_contracts = [configured_event_contract] if configured_event_contract else []
        all_contracts = contracts + template_contracts + event_contracts
        query_shape_variants = diagnostics.get("query_shape_variants", [])
        unique_template_ids = sorted(
            {contract.get("template_id") or "" for contract in all_contracts if contract.get("template_id")}
        )
        successful_shapes = [
            variant["name"]
            for variant in query_shape_variants
            if variant.get("succeeded") and variant.get("contract_count", 0) > 0
        ]
        wildcard_real_filter_count = max(
            [
                variant.get("contract_count") or 0
                for variant in query_shape_variants
                if "wildcard_" in variant.get("name", "")
            ] or [0]
        )
        template_exact_package_id_filter_count = max(
            [
                variant.get("contract_count") or 0
                for variant in query_shape_variants
                if "template_" in variant.get("name", "") and "exact_package_id" in variant.get("name", "")
            ] or [0]
        )

        summary.update(
            {
                "active_contract_endpoint_call_succeeded": True,
                "active_at_offset_used": diagnostics.get("active_at_offset_used"),
                "wildcard_response_shape": diagnostics.get("wildcard_response_shape"),
                "template_response_shape": diagnostics.get("template_response_shape"),
                "query_shape_variants": query_shape_variants,
                "best_query_shape_contract_count": max(
                    [variant.get("contract_count") or 0 for variant in query_shape_variants] or [0]
                ),
                "successful_query_shapes": successful_shapes,
                "wildcard_real_filter_count": wildcard_real_filter_count,
                "template_exact_package_id_filter_count": template_exact_package_id_filter_count,
                "total_number_active_contracts_returned_by_wildcard_query": len(contracts),
                "template_filtered_contract_count": len(template_contracts),
                "unique_template_ids_seen": unique_template_ids,
                "first_10_contract_ids_truncated": [
                    _truncate_contract_id(contract.get("contract_id") or "")
                    for contract in contracts[:10]
                ],
                "first_10_contract_summaries": [
                    {
                        "contract_id": _truncate_contract_id(contract.get("contract_id") or ""),
                        "template_id": contract.get("template_id") or "",
                        "package_name": contract.get("package_name") or "",
                        "module_name": contract.get("module_name") or "",
                        "entity_name": contract.get("entity_name") or "",
                        "signatories": contract.get("signatories") or [],
                        "observers": contract.get("observers") or [],
                        "payload_keys": contract.get("payload_keys") or [],
                    }
                    for contract in contracts[:10]
                ],
                "configured_zalary_config_contract_id_found_in_wildcard_results": bool(
                    configured_contract_id
                    and any(contract.get("contract_id") == configured_contract_id for contract in contracts)
                ),
                "configured_zalary_config_contract_id_found": bool(
                    configured_contract_id
                    and any(contract.get("contract_id") == configured_contract_id for contract in all_contracts)
                ),
                "configured_contract_event_query": configured_contract_event_query or None,
                "configured_contract_event_query_found": bool(configured_event_contract),
                "configured_contract_event_archived_present": bool(
                    configured_contract_event_query.get("archived_present")
                ),
                "template_id_contains": {
                    DEFAULT_PACKAGE_ID: _template_contains(all_contracts, DEFAULT_PACKAGE_ID),
                    "Zalary.Platform": _template_contains(all_contracts, "Zalary.Platform"),
                    "ZalaryConfig": _template_contains(all_contracts, "ZalaryConfig"),
                    "zalary-usdcx-contracts": _template_contains(all_contracts, "zalary-usdcx-contracts"),
                },
                "zalary_config_like_contract_count": sum(
                    1
                    for contract in all_contracts
                    if (
                        "Zalary.Platform" in str(contract.get("template_id") or "")
                        or contract.get("module_name") == "Zalary.Platform"
                    )
                    and (
                        "ZalaryConfig" in str(contract.get("template_id") or "")
                        or contract.get("entity_name") == "ZalaryConfig"
                    )
                ),
            }
        )

        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
