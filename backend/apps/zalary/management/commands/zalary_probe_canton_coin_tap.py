import json
import os
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin, urlparse

from django.core.management.base import BaseCommand
import requests

from apps.zalary.services.auth import (
    LEDGER_API_AUDIENCE,
    LEDGER_API_CLIENT_ID,
    LEDGER_API_TOKEN_SCOPE,
    LEDGER_API_TOKEN_URL,
    LEDGER_API_URL,
    PLATFORM_ADMIN_PARTY,
    default_read_parties,
    load_ledger_auth_settings,
)
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.ledger import LedgerClient
from apps.zalary.services.payloads import decimal_to_daml
from apps.zalary.services.token_transfers.usdcx import (
    DEFAULT_HOLDING_INTERFACE_ID,
    ConfiguredUSDCxTransferProvider,
)


AMULET_TAP_AMOUNT = "10.0000000000"
AMULET_TAP_REFERENCE = "zalary-cc-tap-probe-001"

OPTIONAL_API_URLS = {
    "validator_app_api_url": ("ZALARY_VALIDATOR_APP_API_URL", "ZALARY_VALIDATOR_API_URL"),
    "wallet_app_api_url": ("ZALARY_WALLET_APP_API_URL", "ZALARY_WALLET_API_URL"),
    "scan_proxy_url": ("ZALARY_SCAN_PROXY_URL", "ZALARY_SCAN_API_URL"),
    "cc_tap_api_url": ("ZALARY_CC_TAP_API_URL",),
}

TAP_ENDPOINT_CANDIDATES = (
    "/api/validator/v0/wallet/tap",
    "/api/validator/v0/amulet/tap",
    "/api/validator/v0/faucet/tap",
    "/api/validator/v0/faucet/amulet",
    "/api/wallet/v0/tap",
    "/api/wallet/v0/amulet/tap",
    "/v0/wallet/tap",
    "/v0/amulet/tap",
    "/v0/tap",
)


class Command(BaseCommand):
    help = "Safely probe whether current DevNet credentials can read or tap Canton Coin/Amulet."

    def add_arguments(self, parser):
        parser.add_argument("--employer-party", default="")
        parser.add_argument("--amount", default=AMULET_TAP_AMOUNT)
        parser.add_argument("--reference", default=AMULET_TAP_REFERENCE)
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        employer_party = _selected_employer_party(options.get("employer_party"))
        result = {
            "status": "ok",
            "config": _safe_config_summary(employer_party),
            "initial_balance": {},
            "tap": {
                "method_attempted": "",
                "endpoint_or_function": "",
                "status": "not_attempted",
                "update_id": "",
                "error": "",
            },
            "final_balance": {},
            "conclusion": {},
        }

        try:
            initial_balance = _holding_v1_amulet_balance(employer_party)
            result["initial_balance"] = initial_balance
        except ZalaryBackendError as exc:
            result["status"] = "error"
            result["initial_balance"] = {
                "can_query_holding_v1": False,
                "error": safe_error_message(exc),
            }
            result["conclusion"] = _conclusion(
                tap_success=False,
                tap_attempted=False,
                tap_configured=False,
                auth_or_party_error=False,
            )
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True))
            return

        tap_configured = _tap_configured()
        tap_result = _try_tap_if_configured(
            employer_party=employer_party,
            amount=options["amount"],
            reference=options["reference"],
        )
        result["tap"] = tap_result

        if tap_result["status"] == "success":
            try:
                result["final_balance"] = _holding_v1_amulet_balance(employer_party)
            except ZalaryBackendError as exc:
                result["final_balance"] = {
                    "can_query_holding_v1": False,
                    "error": safe_error_message(exc),
                }
        else:
            result["final_balance"] = initial_balance

        result["conclusion"] = _conclusion(
            tap_success=tap_result["status"] == "success",
            tap_attempted=tap_result["status"] not in {"not_attempted", "skipped_no_config"},
            tap_configured=tap_configured,
            auth_or_party_error=tap_result["status"] in {"unauthorized", "forbidden"},
        )
        self.stdout.write(json.dumps(result, indent=2, sort_keys=True))


def _safe_config_summary(employer_party: str) -> dict[str, Any]:
    configured_urls = {
        label: _safe_url_config(names)
        for label, names in OPTIONAL_API_URLS.items()
    }
    read_parties = default_read_parties()
    return {
        "ledger_api_url_configured": bool(os.environ.get(LEDGER_API_URL, "").strip()),
        "ledger_api_host": _host(os.environ.get(LEDGER_API_URL, "")),
        "token_url_host": _host(os.environ.get(LEDGER_API_TOKEN_URL, "")),
        "auth_client_id_name": os.environ.get(LEDGER_API_CLIENT_ID, "").strip(),
        "auth_audience_name": os.environ.get(LEDGER_API_AUDIENCE, "").strip(),
        "auth_token_scope": os.environ.get(LEDGER_API_TOKEN_SCOPE, "").strip(),
        "validator_app_api_url_configured": configured_urls["validator_app_api_url"]["configured"],
        "validator_app_api_host": configured_urls["validator_app_api_url"]["host"],
        "wallet_app_api_url_configured": configured_urls["wallet_app_api_url"]["configured"],
        "wallet_app_api_host": configured_urls["wallet_app_api_url"]["host"],
        "scan_proxy_url_configured": configured_urls["scan_proxy_url"]["configured"],
        "scan_proxy_host": configured_urls["scan_proxy_url"]["host"],
        "cc_tap_api_url_configured": configured_urls["cc_tap_api_url"]["configured"],
        "cc_tap_api_host": configured_urls["cc_tap_api_url"]["host"],
        "configured_primary_party": read_parties[0] if read_parties else "",
        "platform_admin_party": os.environ.get(PLATFORM_ADMIN_PARTY, "").strip(),
        "employer_party_used_for_testing": employer_party,
    }


def _holding_v1_amulet_balance(employer_party: str) -> dict[str, Any]:
    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    provider = ConfiguredUSDCxTransferProvider(provider_mode="token_standard")
    parties = _dedupe([employer_party, *default_read_parties()])
    contracts = client.query_active_contracts_by_interface(
        interface_id=DEFAULT_HOLDING_INTERFACE_ID,
        parties=parties,
    )

    owner_holdings = []
    holdings = []
    for contract in contracts:
        candidate = provider._holding_candidate_from_contract(contract)
        if candidate is None or candidate.owner != employer_party:
            continue
        owner_holdings.append(candidate)
        if not _is_amulet_instrument(candidate.instrument):
            continue
        holdings.append(candidate)

    unlocked = [holding for holding in holdings if not holding.locked]
    locked = [holding for holding in holdings if holding.locked]
    total = sum((holding.amount for holding in holdings), Decimal("0"))
    instruments = {}
    for holding in holdings:
        instrument_id = _instrument_id(holding.instrument)
        instrument_admin = _instrument_admin(holding.instrument)
        key = f"{instrument_id}|{instrument_admin}"
        instruments[key] = {
            "instrumentId.id": instrument_id,
            "instrumentId.admin": instrument_admin,
        }

    return {
        "can_query_holding_v1": True,
        "holding_v1_contracts_returned": len(contracts),
        "owner_holding_v1_count": len(owner_holdings),
        "owner_instruments_seen": _instrument_summaries(owner_holdings),
        "cc_holdings_count": len(holdings),
        "current_total_cc_balance": decimal_to_daml(total),
        "unlocked_holdings_count": len(unlocked),
        "locked_holdings_count": len(locked),
        "unlocked_cc_balance": decimal_to_daml(sum((holding.amount for holding in unlocked), Decimal("0"))),
        "locked_cc_balance": decimal_to_daml(sum((holding.amount for holding in locked), Decimal("0"))),
        "instruments_seen": list(instruments.values()),
        "holdings": [
            {
                "contract_id_truncated": _truncate_contract_id(holding.contract_id),
                "amount": decimal_to_daml(holding.amount),
                "owner": holding.owner,
                "instrumentId.id": _instrument_id(holding.instrument),
                "instrumentId.admin": _instrument_admin(holding.instrument),
                "locked": holding.locked,
            }
            for holding in holdings[:20]
        ],
    }


def _instrument_summaries(holdings) -> list[dict[str, str]]:
    instruments = {}
    for holding in holdings:
        instrument_id = _instrument_id(holding.instrument)
        instrument_admin = _instrument_admin(holding.instrument)
        key = f"{instrument_id}|{instrument_admin}"
        instruments[key] = {
            "instrumentId.id": instrument_id,
            "instrumentId.admin": instrument_admin,
        }
    return list(instruments.values())[:20]


def _try_tap_if_configured(*, employer_party: str, amount: str, reference: str) -> dict[str, Any]:
    configured_urls = _tap_base_urls()
    if not configured_urls:
        return {
            "method_attempted": "wallet_sdk/validator_api/route_discovery",
            "endpoint_or_function": "",
            "status": "skipped_no_config",
            "update_id": "",
            "error": "No wallet app API URL, validator app API URL, scan proxy URL, or explicit CC tap API URL is configured.",
            "route_discovery": [],
        }

    discovery = _discover_tap_routes(configured_urls)
    explicit_url = os.environ.get("ZALARY_CC_TAP_API_URL", "").strip()
    endpoint = explicit_url or _first_discovered_endpoint(discovery)
    if not endpoint:
        return {
            "method_attempted": "safe_get_options_route_discovery",
            "endpoint_or_function": "",
            "status": "not_found",
            "update_id": "",
            "error": "No plausible Amulet tap/faucet endpoint responded as available.",
            "route_discovery": discovery,
        }

    tap_response = _post_tap(endpoint=endpoint, employer_party=employer_party, amount=amount, reference=reference)
    tap_response["route_discovery"] = discovery
    return tap_response


def _discover_tap_routes(base_urls: list[str]) -> list[dict[str, Any]]:
    discovered = []
    headers = _safe_auth_headers()
    for base_url in base_urls:
        for path in TAP_ENDPOINT_CANDIDATES:
            endpoint = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
            item = {
                "method": "OPTIONS",
                "endpoint": _safe_endpoint_label(endpoint),
                "http_status": None,
                "available_candidate": False,
                "error": "",
            }
            try:
                response = requests.options(endpoint, headers=headers, timeout=10)
            except requests.RequestException:
                item["error"] = "network_error"
                discovered.append(item)
                continue
            item["http_status"] = response.status_code
            item["available_candidate"] = response.status_code not in {404, 410}
            discovered.append(item)
    return discovered


def _post_tap(*, endpoint: str, employer_party: str, amount: str, reference: str) -> dict[str, Any]:
    payload = {
        "party": employer_party,
        "receiver": employer_party,
        "amount": decimal_to_daml(amount),
        "reference": reference,
    }
    try:
        response = requests.post(endpoint, headers=_safe_auth_headers(), json=payload, timeout=20)
    except requests.RequestException:
        return {
            "method_attempted": "validator_or_wallet_api_tap",
            "endpoint_or_function": _safe_endpoint_label(endpoint),
            "status": "network_error",
            "update_id": "",
            "error": "Tap request failed due to a network error.",
        }

    body = _safe_json_body(response)
    update_id = _find_first_string(body, ("updateId", "update_id", "transactionId", "transaction_id"))
    if 200 <= response.status_code < 300:
        return {
            "method_attempted": "validator_or_wallet_api_tap",
            "endpoint_or_function": _safe_endpoint_label(endpoint),
            "status": "success",
            "http_status": response.status_code,
            "update_id": update_id or "",
            "error": "",
        }
    status_label = "failed"
    if response.status_code == 401:
        status_label = "unauthorized"
    elif response.status_code == 403:
        status_label = "forbidden"
    elif response.status_code == 404:
        status_label = "not_found"
    return {
        "method_attempted": "validator_or_wallet_api_tap",
        "endpoint_or_function": _safe_endpoint_label(endpoint),
        "status": status_label,
        "http_status": response.status_code,
        "update_id": update_id or "",
        "error": _safe_error_from_body(body) or f"Tap request failed with HTTP {response.status_code}.",
    }


def _tap_configured() -> bool:
    return bool(_tap_base_urls())


def _tap_base_urls() -> list[str]:
    values = []
    for names in OPTIONAL_API_URLS.values():
        for name in names:
            value = os.environ.get(name, "").strip()
            if value:
                values.append(value)
                break
    return _dedupe(values)


def _safe_auth_headers() -> dict[str, str]:
    from apps.zalary.services.auth import build_auth_headers

    return build_auth_headers(load_ledger_auth_settings())


def _selected_employer_party(value: str | None) -> str:
    explicit = str(value or "").strip()
    if explicit:
        return explicit
    for name in ("ZALARY_EMPLOYER_PARTY", "ZALARY_LEDGER_PARTY", "ZALARY_PLATFORM_ADMIN_PARTY"):
        env_value = os.environ.get(name, "").strip()
        if env_value:
            return env_value
    read_parties = default_read_parties()
    return read_parties[0] if read_parties else ""


def _safe_url_config(names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return {"configured": True, "host": _host(value)}
    return {"configured": False, "host": ""}


def _conclusion(
    *,
    tap_success: bool,
    tap_attempted: bool,
    tap_configured: bool,
    auth_or_party_error: bool,
) -> dict[str, str]:
    if tap_success:
        return {
            "code": "A",
            "text": "Programmatic CC tap works.",
            "next_step": "Build CantonCoinDevNetFaucetService.",
        }
    if auth_or_party_error:
        return {
            "code": "B",
            "text": "Tap exists but requires different auth/party.",
            "next_step": "Update credentials or use the correct validator/wallet user.",
        }
    if tap_attempted or tap_configured:
        return {
            "code": "C",
            "text": "Tap is not exposed to backend credentials.",
            "next_step": "Employer manually taps CC in wallet UI; backend only reads balance and transfers.",
        }
    return {
        "code": "D",
        "text": "No CC faucet/tap found in this 5N environment.",
        "next_step": "Use a pre-funded CC treasury party or keep ZUSD as backup.",
    }


def _is_amulet_instrument(instrument: dict[str, Any]) -> bool:
    text = json.dumps(instrument, sort_keys=True, default=str).lower()
    return "amulet" in text or "canton coin" in text or "cantoncoin" in text


def _instrument_id(instrument: dict[str, Any]) -> str:
    nested = instrument.get("instrumentId")
    if isinstance(nested, dict):
        return str(nested.get("id") or nested.get("instrumentId") or "")
    return str(instrument.get("instrumentId") or instrument.get("id") or instrument.get("symbol") or "")


def _instrument_admin(instrument: dict[str, Any]) -> str:
    nested = instrument.get("instrumentId")
    if isinstance(nested, dict):
        return str(nested.get("admin") or nested.get("instrumentAdmin") or "")
    return str(instrument.get("instrumentAdmin") or instrument.get("admin") or "")


def _host(url: str) -> str:
    parsed = urlparse(str(url or ""))
    return parsed.netloc or ""


def _safe_endpoint_label(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _safe_json_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"message": "non_json_response"}


def _safe_error_from_body(body: Any) -> str:
    found = _find_first_string(body, ("error", "message", "detail", "title"))
    if not found:
        return ""
    return " ".join(found.split())[:240]


def _find_first_string(value: Any, keys: tuple[str, ...]) -> str:
    if isinstance(value, list):
        for item in value:
            found = _find_first_string(item, keys)
            if found:
                return found
        return ""
    if not isinstance(value, dict):
        return ""
    for key in keys:
        item = value.get(key)
        if item is not None:
            return str(item)
    for item in value.values():
        found = _find_first_string(item, keys)
        if found:
            return found
    return ""


def _truncate_contract_id(contract_id: str) -> str:
    value = str(contract_id or "")
    if len(value) <= 24:
        return value
    return f"{value[:12]}...{value[-12:]}"


def _first_discovered_endpoint(discovery: list[dict[str, Any]]) -> str:
    for item in discovery:
        if item.get("available_candidate") and item.get("http_status") not in {401, 403}:
            return item.get("endpoint") or ""
    return ""


def _dedupe(values):
    seen = set()
    deduped = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped
