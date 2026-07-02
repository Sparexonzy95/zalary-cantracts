import json
import os
import re
from typing import Any, Iterable, Sequence
from urllib.parse import urljoin

from django.core.management.base import BaseCommand, CommandError
import requests

from apps.zalary.services.auth import (
    LEDGER_PARTY,
    auth_configured,
    build_auth_headers,
    default_read_parties,
    env_flag_enabled,
    ledger_api_url_configured,
    load_ledger_auth_settings,
)
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.ledger import (
    ACTIVE_CONTRACTS_ENDPOINT,
    LedgerClient,
    normalize_active_contracts_response,
    template_display_text,
)
from apps.zalary.services.payloads import decimal_to_daml
from apps.zalary.services.token_transfers import factory as transfer_factory
from apps.zalary.services.token_transfers.base import TokenTransferRequest
from apps.zalary.services.token_transfers.usdcx import (
    DEFAULT_HOLDING_INTERFACE_ID,
    DEFAULT_TRANSFER_FACTORY_INTERFACE_ID,
    DEFAULT_TRANSFER_INSTRUCTION_INTERFACE_ID,
    ConfiguredUSDCxTransferProvider,
    InsufficientHoldingsError,
)


KEYWORD_RE = re.compile(
    r"(registry|utility|endpoint|url|disclosure|transfer|factory|admin|instrument|token|issuer)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s\"'<>)}\]]+")


class Command(BaseCommand):
    help = (
        "Safely diagnose whether visible P2PLending USDCx holdings expose Token Standard "
        "TransferFactory/TransferInstruction contracts or custom transfer hints."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--employer-party",
            default="",
            help="Employer/sender Party ID. Defaults to ZALARY_LEDGER_PARTY/default read party.",
        )
        parser.add_argument(
            "--employee-party",
            default="",
            help="Employee/receiver Party ID for TransferInstruction visibility. Defaults to employer party.",
        )
        parser.add_argument("--amount", default="1.0000000000")
        parser.add_argument("--instrument-id", default="USDCx")
        parser.add_argument(
            "--instrument-admin",
            required=True,
            help="USDCx instrument admin Party ID.",
        )
        parser.add_argument("--max-contracts", type=int, default=10)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        employer_party = _party_from_options(options)
        employee_party = (options["employee_party"] or employer_party).strip()
        provider = _configured_provider()
        request = _request_from_options(options, employer_party=employer_party, employee_party=employee_party)

        try:
            result = diagnose(
                provider=provider,
                request=request,
                employer_party=employer_party,
                employee_party=employee_party,
                max_contracts=max(options["max_contracts"], 0),
            )
        except (ZalaryBackendError, requests.RequestException, ValueError) as exc:
            payload = {
                "status": "error",
                "ledger_api_url_configured": ledger_api_url_configured(),
                "auth_configured": auth_configured(),
                "error": safe_error_message(exc),
            }
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            raise CommandError("USDCx P2PLending diagnostics failed.") from exc

        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))


def diagnose(
    *,
    provider: ConfiguredUSDCxTransferProvider,
    request: TokenTransferRequest,
    employer_party: str,
    employee_party: str,
    max_contracts: int,
) -> dict[str, Any]:
    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    read_parties = _dedupe([employer_party, *default_read_parties()])
    instruction_read_parties = _dedupe([employer_party, employee_party, *default_read_parties()])

    holding_contracts = client.query_active_contracts_by_interface(
        interface_id=provider.holding_interface_id,
        parties=read_parties,
    )
    holding_by_id = {
        str(contract.get("contract_id") or ""): contract
        for contract in holding_contracts
        if contract.get("contract_id")
    }
    holdings = provider.list_usdcx_holdings(request)
    selected = provider.select_input_holdings(holdings, request.amount)
    selected_holding = selected[0] if selected else None
    selected_contract = holding_by_id.get(selected_holding.contract_id if selected_holding else "")

    if selected_holding is not None and selected_contract is None:
        refetched_contracts = client.query_active_contracts_by_interface(
            interface_id=provider.holding_interface_id,
            parties=read_parties,
        )
        for contract in refetched_contracts:
            if contract.get("contract_id") == selected_holding.contract_id:
                selected_contract = contract
                break

    if selected_holding is None or selected_contract is None:
        raise InsufficientHoldingsError("No matching visible USDCx holding could be selected.")

    transfer_factory_contracts = client.query_active_contracts_by_interface(
        interface_id=provider.transfer_factory_interface_id,
        parties=read_parties,
    )
    transfer_instruction_contracts = client.query_active_contracts_by_interface(
        interface_id=provider.transfer_instruction_interface_id,
        parties=instruction_read_parties,
    )

    template_info = selected_contract.get("template_id") or {}
    package_id = str(template_info.get("package_id") or "")
    holding_summary = _holding_contract_summary(
        selected_contract,
        selected_holding=selected_holding,
        holding_interface_id=provider.holding_interface_id,
    )
    holding_view_summary = _holding_interface_view_summary(
        selected_contract,
        holding_interface_id=provider.holding_interface_id,
    )
    registry_contracts = _query_template_by_package_id(
        settings=settings,
        package_id=package_id,
        module_name="P2PLending.Token.USDCx",
        entity_name="USDCxRegistry",
        parties=read_parties,
    )
    package_summary = _diagnose_package_metadata(settings=settings, package_id=package_id)
    history_summary = _diagnose_transaction_history(
        settings=settings,
        parties=instruction_read_parties,
        holding_interface_id=provider.holding_interface_id,
        selected_contract_id=selected_holding.contract_id,
    )

    factory_summary = _interface_contract_summary(
        transfer_factory_contracts,
        max_contracts=max_contracts,
    )
    instruction_summary = _interface_contract_summary(
        transfer_instruction_contracts,
        max_contracts=max_contracts,
    )

    p2p_choices = package_summary.get("custom_transfer_like_choices") or []
    conclusion = _conclusion(
        transfer_factory_count=len(transfer_factory_contracts),
        package_summary=package_summary,
        custom_transfer_like_choices=p2p_choices,
    )

    return {
        "status": "ok",
        "ledger_api_url_configured": ledger_api_url_configured(),
        "auth_configured": auth_configured(),
        "employer_party": employer_party,
        "employee_party": employee_party,
        "holding_interface_id": provider.holding_interface_id,
        "transfer_factory_interface_id": provider.transfer_factory_interface_id,
        "transfer_instruction_interface_id": provider.transfer_instruction_interface_id,
        "matching_holding_count": len(holdings),
        "selected_holding_amount": decimal_to_daml(selected_holding.amount),
        "selected_holding": holding_summary,
        "holding_interface_view": holding_view_summary,
        "transfer_factory_acs": {
            "count": len(transfer_factory_contracts),
            **factory_summary,
        },
        "transfer_instruction_acs": {
            "count": len(transfer_instruction_contracts),
            "pending_instruction_exists": bool(transfer_instruction_contracts),
            **instruction_summary,
        },
        "usdcx_registry_acs": {
            "count": len(registry_contracts),
            **_interface_contract_summary(registry_contracts, max_contracts=max_contracts),
        },
        "p2plending_package": package_summary,
        "transaction_history": history_summary,
        "standard_transfer_factory_usage_seen": _history_contains(
            history_summary,
            "TransferFactory_Transfer",
        ),
        "wallet_proxy_transfer_factory_usage_seen": _history_contains(
            history_summary,
            "WalletUserProxy_TransferFactory_Transfer",
        ),
        "custom_p2plending_transfer_choices_found": p2p_choices,
        "conclusion": conclusion,
        "next_step": _next_step(conclusion, p2p_choices),
        "submitted_commands": False,
        "salary_claim_confirm_settlement_called": False,
    }


def _party_from_options(options: dict[str, Any]) -> str:
    party = (options.get("employer_party") or "").strip()
    if party:
        return party
    party = os.environ.get(LEDGER_PARTY, "").strip()
    if party:
        return party
    read_parties = default_read_parties()
    if read_parties:
        return read_parties[0]
    raise CommandError("An employer party is required for USDCx diagnostics.")


def _configured_provider() -> ConfiguredUSDCxTransferProvider:
    provider = transfer_factory.get_token_transfer_provider()
    if isinstance(provider, ConfiguredUSDCxTransferProvider):
        return provider
    return ConfiguredUSDCxTransferProvider(
        utility_api_url=_env(transfer_factory.USDCX_UTILITY_API_URL),
        xreserve_api_url=_env(transfer_factory.USDCX_XRESERVE_API_URL),
        provider_mode="token_standard",
        timeout_seconds=_timeout_seconds(),
        holding_interface_id=_env(transfer_factory.USDCX_HOLDING_INTERFACE_ID) or DEFAULT_HOLDING_INTERFACE_ID,
        transfer_factory_interface_id=(
            _env(transfer_factory.USDCX_TRANSFER_FACTORY_INTERFACE_ID)
            or DEFAULT_TRANSFER_FACTORY_INTERFACE_ID
        ),
        transfer_instruction_interface_id=(
            _env(transfer_factory.USDCX_TRANSFER_INSTRUCTION_INTERFACE_ID)
            or DEFAULT_TRANSFER_INSTRUCTION_INTERFACE_ID
        ),
        transfer_factory_endpoint=_env(transfer_factory.USDCX_TRANSFER_FACTORY_ENDPOINT),
        auto_accept_pending_transfer=env_flag_enabled(
            transfer_factory.USDCX_AUTO_ACCEPT_PENDING_TRANSFER,
            default=False,
        ),
        allow_canonical_transfer_argument=env_flag_enabled(
            transfer_factory.USDCX_ALLOW_CANONICAL_TRANSFER_ARGUMENT,
            default=False,
        ),
        transfer_argument_shape=_env(transfer_factory.USDCX_TRANSFER_ARGUMENT_SHAPE),
    )


def _request_from_options(
    options: dict[str, Any],
    *,
    employer_party: str,
    employee_party: str,
) -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id="p2plending-diagnostics",
        payroll_id="p2plending-diagnostics",
        employee_external_id="p2plending-diagnostics",
        salary_claim_contract_id="",
        token={
            "symbol": "USDCx",
            "instrumentId": options["instrument_id"],
            "instrumentAdmin": options["instrument_admin"],
        },
        sender_party=employer_party,
        receiver_party=employee_party,
        amount=options["amount"],
        transfer_reference="zalary-p2plending-diagnostics",
    )


def _holding_contract_summary(
    contract: dict[str, Any],
    *,
    selected_holding: Any,
    holding_interface_id: str,
) -> dict[str, Any]:
    template_info = contract.get("template_id") or {}
    payload = _dict_or_empty(contract.get("payload"))
    view = _selected_interface_view(contract, holding_interface_id)
    combined = _deep_merge(payload, view)
    metadata = _metadata_summary(combined)
    return {
        "contract_id_truncated": _truncate_contract_id(str(contract.get("contract_id") or "")),
        "template_id": template_display_text(template_info),
        "package_id": template_info.get("package_id") or "",
        "module_name": template_info.get("module_name") or "",
        "entity_name": template_info.get("entity_name") or "",
        "signatories": contract.get("signatories") or [],
        "observers": contract.get("observers") or [],
        "payload_keys": sorted(payload.keys()),
        "interface_view_keys": sorted(view.keys()),
        "owner_field": _first_value_by_keys(combined, ("owner", "accountOwner", "holder", "party")),
        "amount_field": decimal_to_daml(selected_holding.amount),
        "lock_field": _first_value_by_keys(combined, ("lock", "locked", "lockContext", "lock_context")),
        "registry_field": _first_value_by_keys(combined, ("registry", "registryUrl", "registry_url")),
        "instrument_id_field": _first_value_by_keys(combined, ("instrumentId", "instrument_id", "id")),
        "instrument_field": _safe_value(_first_value_by_keys(combined, ("instrument", "token", "tokenInstrument"))),
        "instrument_admin_field": _first_value_by_keys(
            combined,
            ("instrumentAdmin", "instrument_admin", "admin", "issuer"),
        ),
        "metadata_keys": metadata["keys"],
        "metadata_values": metadata["values"],
        "url_like_strings": _url_like_strings(combined),
        "keyword_key_paths": _keyword_key_paths(combined),
        "created_event_blob_present": bool(contract.get("created_event_blob")),
    }


def _holding_interface_view_summary(contract: dict[str, Any], *, holding_interface_id: str) -> dict[str, Any]:
    view = _selected_interface_view(contract, holding_interface_id)
    metadata = _metadata_summary(view)
    return {
        "exists": bool(view),
        "view_field_names": sorted(view.keys()),
        "instrument_id": _first_value_by_keys(view, ("instrumentId", "instrument_id", "id")),
        "instrument_admin": _first_value_by_keys(view, ("instrumentAdmin", "instrument_admin", "admin", "issuer")),
        "metadata_values": metadata["values"],
        "url_like_strings": _url_like_strings(view),
        "created_event_blob_present": bool(contract.get("created_event_blob")),
    }


def _interface_contract_summary(contracts: Sequence[dict[str, Any]], *, max_contracts: int) -> dict[str, Any]:
    templates = sorted(
        {
            template_display_text(contract.get("template_id") or {})
            for contract in contracts
            if template_display_text(contract.get("template_id") or {})
        }
    )
    contract_summaries = []
    p2plending_factories = []
    registry_admin_instrument_fields = []
    for contract in contracts[:max_contracts]:
        template_info = contract.get("template_id") or {}
        payload = _dict_or_empty(contract.get("payload"))
        view = _all_interface_views(contract)
        combined = _deep_merge(payload, view)
        template_id = template_display_text(template_info)
        item = {
            "contract_id_truncated": _truncate_contract_id(str(contract.get("contract_id") or "")),
            "template_id": template_id,
            "package_id": template_info.get("package_id") or "",
            "module_name": template_info.get("module_name") or "",
            "entity_name": template_info.get("entity_name") or "",
            "payload_keys": sorted(payload.keys()),
            "interface_view_keys": sorted(view.keys()),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "keyword_key_paths": _keyword_key_paths(combined),
        }
        contract_summaries.append(item)
        if "P2PLending" in template_id:
            p2plending_factories.append(item)
        registry_admin_instrument_fields.extend(_keyword_key_paths(combined))
    return {
        "templates": templates,
        "contracts": contract_summaries,
        "p2plending_related_contracts": p2plending_factories,
        "registry_admin_instrument_key_paths": sorted(set(registry_admin_instrument_fields)),
    }


def _diagnose_package_metadata(*, settings: Any, package_id: str) -> dict[str, Any]:
    if not package_id:
        return {
            "package_id": "",
            "endpoint_attempts": [],
            "modules_beginning_p2plending": [],
            "templates_in_p2plending_token_usdcx": [],
            "choices_on_usdcxholding": [],
            "implements_holding_v1": False,
            "implements_transfer_factory": False,
            "implements_transfer_instruction": False,
            "custom_transfer_like_choices": [],
            "blocker": "Selected holding did not expose a package id.",
        }

    endpoint_attempts = []
    text_corpus = ""
    for path in (
        f"v2/packages/{package_id}",
        f"v2/packages/{package_id}/archive",
        f"v2/packages/{package_id}/status",
    ):
        result = _safe_get(settings=settings, path=path)
        endpoint_attempts.append(_safe_endpoint_attempt(result))
        if result.get("succeeded"):
            text_corpus += "\n" + _safe_response_corpus(result.get("_body"), result.get("_content") or b"")

    modules = sorted(set(re.findall(r"P2PLending(?:\.[A-Za-z0-9_]+)+", text_corpus)))
    usdcx_mentions = sorted(
        {
            item
            for item in re.findall(r"[A-Za-z0-9_.:#-]*USDCx[A-Za-z0-9_.:#-]*", text_corpus)
            if len(item) <= 160 and not item.startswith(("$$", "$$$", ".", "0", "2", "3", "6", "7", "8", "9"))
        }
    )
    choices_on_usdcxholding = _plain_symbol_names(
        text_corpus,
        prefixes=("USDCxHolding_",),
    )
    registry_transfer_choices = _plain_symbol_names(
        text_corpus,
        exact_names=("USDCx_Transfer", "USDCx_TransferByOwner"),
    )
    custom_transfer_like = registry_transfer_choices

    return {
        "package_id": package_id,
        "endpoint_attempts": endpoint_attempts,
        "modules_beginning_p2plending": modules,
        "templates_in_p2plending_token_usdcx": usdcx_mentions,
        "choices_on_usdcxholding": choices_on_usdcxholding,
        "registry_transfer_choices": registry_transfer_choices,
        "implements_holding_v1": "Splice.Api.Token.HoldingV1" in text_corpus or "HoldingV1" in text_corpus,
        "implements_transfer_factory": "TransferFactory" in text_corpus,
        "implements_transfer_instruction": "TransferInstruction" in text_corpus,
        "custom_transfer_like_choices": custom_transfer_like,
        "blocker": "" if any(attempt.get("succeeded") for attempt in endpoint_attempts) else "Package metadata was not exposed through tested Ledger API package endpoints.",
    }


def _diagnose_transaction_history(
    *,
    settings: Any,
    parties: Sequence[str],
    holding_interface_id: str,
    selected_contract_id: str,
) -> dict[str, Any]:
    ledger_end = {}
    try:
        ledger_end = LedgerClient(settings).get_current_ledger_offset()
    except ZalaryBackendError:
        ledger_end = {}
    end_offset = str(ledger_end.get("offset") or ledger_end.get("ledgerEnd") or ledger_end.get("ledger_end") or "")
    event_format = _event_format_for_interface(parties=parties, interface_id=holding_interface_id)
    contract_event_format = _event_format_for_wildcard(parties=parties)
    bodies = [
        {
            "name": "updates_transactions_update_format",
            "path": "v2/updates/transactions",
            "body": {
                "beginExclusive": "0",
                "endInclusive": end_offset,
                "updateFormat": {
                    "includeTransactions": {
                        "transactionShape": "TRANSACTION_SHAPE_ACS_DELTA",
                        "eventFormat": event_format,
                    }
                },
            },
        },
        {
            "name": "updates_trees_event_format",
            "path": "v2/updates/trees",
            "body": {
                "beginExclusive": "0",
                "endInclusive": end_offset,
                "updateFormat": {
                    "includeTransactionTrees": {
                        "eventFormat": event_format,
                    }
                },
            },
        },
        {
            "name": "events_by_selected_contract_id",
            "path": "v2/events/events-by-contract-id",
            "body": {
                "contractId": selected_contract_id,
                "requestingParties": list(parties),
                "eventFormat": contract_event_format,
            },
        },
    ]

    attempts = []
    choice_names = set()
    template_ids = set()
    holding_changed_by_transfer = False
    for item in bodies:
        result = _safe_post(settings=settings, path=item["path"], body=item["body"])
        attempt = _safe_endpoint_attempt(result)
        attempt["name"] = item["name"]
        attempts.append(attempt)
        corpus = _safe_response_corpus(result.get("_body"), result.get("_content") or b"")
        choice_names.update(_choice_names_from_text(corpus))
        for contract in normalize_active_contracts_response(result.get("_body") or {}):
            template_id = template_display_text(contract.get("template_id") or {})
            if template_id:
                template_ids.add(template_id)
        if "archived" in corpus.lower() and "transfer" in corpus.lower():
            holding_changed_by_transfer = True

    return {
        "ledger_end_seen": bool(end_offset),
        "query_attempts": attempts,
        "choice_names_seen": sorted(choice_names),
        "template_ids_seen": sorted(template_ids),
        "holding_changed_by_transfer": holding_changed_by_transfer,
        "standard_or_custom": _history_standard_or_custom(choice_names),
    }


def _safe_get(*, settings: Any, path: str) -> dict[str, Any]:
    endpoint = urljoin(settings.ledger_api_url.rstrip("/") + "/", path)
    try:
        response = requests.get(
            endpoint,
            headers=build_auth_headers(settings),
            timeout=settings.timeout_seconds,
            verify=settings.tls_ca_file or True,
        )
    except requests.RequestException:
        return {"succeeded": False, "path": path, "http_status": None, "content_type": "", "error": "network_error"}
    return _safe_response_result(path=path, response=response)


def _safe_post(*, settings: Any, path: str, body: dict[str, Any]) -> dict[str, Any]:
    endpoint = urljoin(settings.ledger_api_url.rstrip("/") + "/", path)
    try:
        response = requests.post(
            endpoint,
            headers={**build_auth_headers(settings), "Content-Type": "application/json"},
            json=body,
            timeout=settings.timeout_seconds,
            verify=settings.tls_ca_file or True,
        )
    except requests.RequestException:
        return {"succeeded": False, "path": path, "http_status": None, "content_type": "", "error": "network_error"}
    return _safe_response_result(path=path, response=response)


def _query_template_by_package_id(
    *,
    settings: Any,
    package_id: str,
    module_name: str,
    entity_name: str,
    parties: Sequence[str],
) -> list[dict[str, Any]]:
    if not package_id:
        return []
    endpoint_path = ACTIVE_CONTRACTS_ENDPOINT
    template_text = f"{package_id}:{module_name}:{entity_name}"
    template_identifier = {
        "packageId": package_id,
        "moduleName": module_name,
        "entityName": entity_name,
    }
    filters = [
        {"identifierFilter": {"TemplateFilter": {"value": {"templateId": template_text}}}},
        {"identifierFilter": {"templateFilter": {"templateId": template_identifier}}},
    ]
    for identifier_filter in filters:
        body = {
            "filter": {
                "filtersByParty": {
                    party: {"cumulative": [identifier_filter]}
                    for party in parties
                }
            },
            "verbose": True,
        }
        result = _safe_post(settings=settings, path=endpoint_path, body=body)
        if not result.get("succeeded"):
            continue
        contracts = normalize_active_contracts_response(result.get("_body") or {})
        matching = [
            contract
            for contract in contracts
            if (contract.get("template_id") or {}).get("module_name") == module_name
            and (contract.get("template_id") or {}).get("entity_name") == entity_name
        ]
        if matching:
            return matching
    return []


def _safe_response_result(*, path: str, response: requests.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    result: dict[str, Any] = {
        "succeeded": response.status_code < 400,
        "path": path,
        "http_status": response.status_code,
        "content_type": content_type.split(";")[0],
        "error": "" if response.status_code < 400 else "http_error",
        "_content": response.content[:1_000_000],
    }
    try:
        result["_body"] = response.json()
    except ValueError:
        result["_body"] = None
    if response.status_code >= 400:
        result["error_detail"] = " ".join(response.text.split())[:160]
    return result


def _safe_endpoint_attempt(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("_body")
    json_keys = sorted(body.keys()) if isinstance(body, dict) else []
    return {
        "path": result.get("path") or "",
        "succeeded": bool(result.get("succeeded")),
        "http_status": result.get("http_status"),
        "content_type": result.get("content_type") or "",
        "json_keys": json_keys,
        "error": result.get("error") or "",
        "error_detail": result.get("error_detail") or "",
    }


def _event_format_for_interface(*, parties: Sequence[str], interface_id: str) -> dict[str, Any]:
    interface_filter = {
        "identifierFilter": {
            "interfaceFilter": {
                "interfaceId": interface_id,
                "includeInterfaceView": True,
                "includeCreatedEventBlob": False,
            }
        }
    }
    return {
        "filtersByParty": {
            party: {"cumulative": [interface_filter]}
            for party in parties
        },
        "verbose": True,
    }


def _event_format_for_wildcard(*, parties: Sequence[str]) -> dict[str, Any]:
    wildcard_filter = {"identifierFilter": {"WildcardFilter": {"value": {}}}}
    return {
        "filtersByParty": {
            party: {"cumulative": [wildcard_filter]}
            for party in parties
        },
        "verbose": True,
    }


def _choice_names_from_text(text: str) -> set[str]:
    candidates = re.findall(
        r"[A-Za-z0-9_.$:-]*(?:TransferFactory_Transfer|WalletUserProxy_TransferFactory_Transfer|"
        r"TransferInstruction_Accept|[A-Za-z0-9_]*Transfer[A-Za-z0-9_]*)",
        text,
    )
    return {candidate for candidate in candidates if 4 <= len(candidate) <= 180}


def _plain_symbol_names(
    text: str,
    *,
    prefixes: Sequence[str] = (),
    exact_names: Sequence[str] = (),
) -> list[str]:
    names = set()
    for exact_name in exact_names:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(exact_name)}(?![A-Za-z0-9_])", text):
            names.add(exact_name)
    for prefix in prefixes:
        for match in re.findall(rf"(?<![A-Za-z0-9_]){re.escape(prefix)}[A-Za-z0-9_]+", text):
            if match.startswith(("sc_", "test")):
                continue
            names.add(match)
    return sorted(names)


def _history_standard_or_custom(choice_names: Iterable[str]) -> str:
    choices = set(choice_names)
    if "TransferFactory_Transfer" in choices or any("TransferFactory_Transfer" in item for item in choices):
        return "standard_token_standard"
    if any("P2PLending" in item and "Transfer" in item for item in choices):
        return "custom_p2plending"
    if choices:
        return "transfer_like_choices_seen"
    return "not_seen"


def _history_contains(history_summary: dict[str, Any], needle: str) -> bool:
    return any(needle in item for item in history_summary.get("choice_names_seen") or [])


def _conclusion(
    *,
    transfer_factory_count: int,
    package_summary: dict[str, Any],
    custom_transfer_like_choices: Sequence[str],
) -> str:
    if transfer_factory_count > 0:
        return "standard_registry_flow_possible_from_visible_transfer_factory_acs"
    if package_summary.get("implements_transfer_factory"):
        return "blocked_pending_5n_registry_url"
    if custom_transfer_like_choices:
        return "custom_p2plending_flow_likely"
    return "blocked_pending_5n_operator_guidance"


def _next_step(conclusion: str, custom_transfer_like_choices: Sequence[str]) -> str:
    if conclusion == "standard_registry_flow_possible_from_visible_transfer_factory_acs":
        return "Attempt a dry-run using the visible TransferFactory contract id only after choice context is discoverable."
    if conclusion == "blocked_pending_5n_registry_url":
        return "Ask the 5N/P2PLending operator for the environment-specific TransferFactory registry URL."
    if conclusion == "custom_p2plending_flow_likely":
        return (
            "Inspect the exact custom choice argument schema before adding "
            "ZALARY_USDCX_TRANSFER_PROVIDER=p2plending_custom. Candidate choices: "
            + ", ".join(custom_transfer_like_choices[:10])
        )
    return "Ask the 5N/P2PLending operator whether USDCx holdings are transferable through Token Standard or a private P2PLending choice."


def _safe_response_corpus(body: Any, content: bytes) -> str:
    if isinstance(body, (dict, list)):
        return json.dumps(body, sort_keys=True)
    if not content:
        return ""
    return content.decode("utf-8", errors="ignore")


def _selected_interface_view(contract: dict[str, Any], interface_id: str) -> dict[str, Any]:
    views = contract.get("interface_views") or {}
    if not isinstance(views, dict):
        return {}
    value = views.get(interface_id) or views.get(DEFAULT_HOLDING_INTERFACE_ID) or next(iter(views.values()), {})
    return _dict_or_empty(value)


def _all_interface_views(contract: dict[str, Any]) -> dict[str, Any]:
    views = contract.get("interface_views") or {}
    if not isinstance(views, dict):
        return {}
    return {
        key: value
        for key, value in views.items()
        if isinstance(value, (dict, list, str, int, float, bool)) or value is None
    }


def _metadata_summary(value: Any) -> dict[str, Any]:
    metadata = _first_value_by_keys(value, ("metadata", "meta"))
    if not isinstance(metadata, dict):
        return {"keys": [], "values": _safe_value(metadata) if metadata is not None else None}
    return {
        "keys": sorted(metadata.keys()),
        "values": _safe_value(metadata),
    }


def _keyword_key_paths(value: Any) -> list[str]:
    paths = []
    for path, _item in _walk(value):
        if path and KEYWORD_RE.search(path[-1]):
            paths.append(".".join(path))
    return sorted(set(paths))


def _url_like_strings(value: Any) -> list[str]:
    urls = []
    for _path, item in _walk(value):
        if isinstance(item, str):
            urls.extend(URL_RE.findall(item))
    return sorted(set(urls))


def _first_value_by_keys(value: Any, keys: Sequence[str]) -> Any:
    wanted = {key.lower() for key in keys}
    for path, item in _walk(value):
        if path and path[-1].lower() in wanted:
            return _safe_value(item)
    return None


def _walk(value: Any, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = (*path, str(key))
            yield item_path, item
            yield from _walk(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, (*path, str(index)))


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe_value(item)
            for key, item in value.items()
            if not _sensitive_key(key)
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:20]]
    if isinstance(value, str):
        if len(value) > 240:
            return value[:120] + "...[truncated]"
        return value
    return value


def _sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return "secret" in lowered or "authorization" in lowered or "auth" == lowered or "bearer" in lowered


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _truncate_contract_id(contract_id: str) -> str:
    if len(contract_id) <= 24:
        return contract_id
    return f"{contract_id[:12]}...{contract_id[-12:]}"


def _dedupe(values: Sequence[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        item = (value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _timeout_seconds() -> int:
    try:
        return max(int(_env(transfer_factory.USDCX_TRANSFER_TIMEOUT_SECONDS) or "60"), 1)
    except ValueError:
        return 60


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()
