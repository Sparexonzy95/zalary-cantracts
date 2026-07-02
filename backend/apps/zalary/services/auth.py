from dataclasses import dataclass
import os
import time

import requests

from .errors import ConfigurationError, LedgerSubmissionError


LEDGER_API_URL = "ZALARY_LEDGER_API_URL"
LEDGER_API_AUTH_TOKEN = "ZALARY_LEDGER_API_AUTH_TOKEN"
LEDGER_API_TOKEN_URL = "ZALARY_LEDGER_API_TOKEN_URL"
LEDGER_API_CLIENT_ID = "ZALARY_LEDGER_API_CLIENT_ID"
LEDGER_API_CLIENT_SECRET = "ZALARY_LEDGER_API_CLIENT_SECRET"
LEDGER_API_AUDIENCE = "ZALARY_LEDGER_API_AUDIENCE"
LEDGER_API_TOKEN_SCOPE = "ZALARY_LEDGER_API_TOKEN_SCOPE"
LEDGER_API_TLS_CA_FILE = "ZALARY_LEDGER_API_TLS_CA_FILE"
LEDGER_API_TIMEOUT_SECONDS = "ZALARY_LEDGER_API_TIMEOUT_SECONDS"

DAML_PACKAGE_NAME = "ZALARY_DAML_PACKAGE_NAME"
DAML_PACKAGE_ID = "ZALARY_DAML_PACKAGE_ID"
LEDGER_PARTY = "ZALARY_LEDGER_PARTY"
PLATFORM_ADMIN_PARTY = "ZALARY_PLATFORM_ADMIN_PARTY"
PLATFORM_CONFIG_CONTRACT_ID = "ZALARY_PLATFORM_CONFIG_CONTRACT_ID"
DEFAULT_ACT_AS = "ZALARY_DEFAULT_ACT_AS"
DEFAULT_READ_AS = "ZALARY_DEFAULT_READ_AS"
COMMAND_ID_PREFIX = "ZALARY_COMMAND_ID_PREFIX"
SYNC_START_OFFSET = "ZALARY_SYNC_START_OFFSET"
SYNC_PAGE_SIZE = "ZALARY_SYNC_PAGE_SIZE"
ALLOW_SINGLE_PARTY_DEMO = "ZALARY_ALLOW_SINGLE_PARTY_DEMO"

ENVIRONMENT_VARIABLES = (
    LEDGER_API_URL,
    LEDGER_API_AUTH_TOKEN,
    LEDGER_API_TOKEN_URL,
    LEDGER_API_CLIENT_ID,
    LEDGER_API_CLIENT_SECRET,
    LEDGER_API_AUDIENCE,
    LEDGER_API_TOKEN_SCOPE,
    LEDGER_API_TLS_CA_FILE,
    LEDGER_API_TIMEOUT_SECONDS,
    DAML_PACKAGE_NAME,
    DAML_PACKAGE_ID,
    LEDGER_PARTY,
    PLATFORM_ADMIN_PARTY,
    PLATFORM_CONFIG_CONTRACT_ID,
    DEFAULT_ACT_AS,
    DEFAULT_READ_AS,
    COMMAND_ID_PREFIX,
    SYNC_START_OFFSET,
    SYNC_PAGE_SIZE,
    ALLOW_SINGLE_PARTY_DEMO,
)


@dataclass(frozen=True)
class LedgerAuthSettings:
    ledger_api_url: str
    auth_token: str | None
    token_url: str | None
    client_id: str | None
    client_secret: str | None
    audience: str | None
    token_scope: str
    tls_ca_file: str | None
    timeout_seconds: int


@dataclass
class CachedBearerToken:
    access_token: str
    expires_at: float
    cache_key: tuple[str | None, str | None, str | None, str]


TOKEN_REFRESH_SKEW_SECONDS = 60
DEFAULT_TOKEN_SCOPE = "daml_ledger_api"
DEFAULT_LEDGER_PARTY = "5nsandbox-devnet-2::1220a14ca128063b8dc9d1ebb0bd22633be9f2168500f4dbc1ecaeb1855b14e5acf8"
_TOKEN_CACHE: CachedBearerToken | None = None


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def load_ledger_auth_settings() -> LedgerAuthSettings:
    ledger_api_url = _optional(LEDGER_API_URL)
    if ledger_api_url is None:
        raise ConfigurationError(f"{LEDGER_API_URL} is required before Ledger API calls are enabled.")

    timeout_value = _optional(LEDGER_API_TIMEOUT_SECONDS) or "30"
    try:
        timeout_seconds = int(timeout_value)
    except ValueError as exc:
        raise ConfigurationError(f"{LEDGER_API_TIMEOUT_SECONDS} must be an integer.") from exc

    return LedgerAuthSettings(
        ledger_api_url=ledger_api_url,
        auth_token=_optional(LEDGER_API_AUTH_TOKEN),
        token_url=_optional(LEDGER_API_TOKEN_URL),
        client_id=_optional(LEDGER_API_CLIENT_ID),
        client_secret=_optional(LEDGER_API_CLIENT_SECRET),
        audience=_optional(LEDGER_API_AUDIENCE),
        token_scope=_optional(LEDGER_API_TOKEN_SCOPE) or DEFAULT_TOKEN_SCOPE,
        tls_ca_file=_optional(LEDGER_API_TLS_CA_FILE),
        timeout_seconds=timeout_seconds,
    )


def ledger_api_url_configured() -> bool:
    return _optional(LEDGER_API_URL) is not None


def auth_configured() -> bool:
    if _optional(LEDGER_API_AUTH_TOKEN):
        return True
    return all(
        _optional(name)
        for name in (
            LEDGER_API_TOKEN_URL,
            LEDGER_API_CLIENT_ID,
            LEDGER_API_CLIENT_SECRET,
        )
    )


def env_flag_enabled(name: str, *, default: bool = False) -> bool:
    value = _optional(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def single_party_demo_enabled() -> bool:
    return env_flag_enabled(ALLOW_SINGLE_PARTY_DEMO, default=False)


def _split_parties(value: str | None) -> list[str]:
    if not value:
        return []
    return [party.strip() for party in value.split(",") if party.strip()]


def default_read_parties() -> list[str]:
    ledger_party = _optional(LEDGER_PARTY)
    if ledger_party:
        return [ledger_party]

    read_as = _split_parties(_optional(DEFAULT_READ_AS))
    if read_as:
        return read_as

    act_as = _split_parties(_optional(DEFAULT_ACT_AS))
    if act_as:
        return act_as

    return [DEFAULT_LEDGER_PARTY]


def _client_credentials_cache_key(settings: LedgerAuthSettings) -> tuple[str | None, str | None, str | None, str]:
    return (
        settings.token_url,
        settings.client_id,
        settings.audience,
        settings.token_scope,
    )


def _cached_token_is_valid(settings: LedgerAuthSettings) -> bool:
    if _TOKEN_CACHE is None:
        return False
    return (
        _TOKEN_CACHE.cache_key == _client_credentials_cache_key(settings)
        and _TOKEN_CACHE.expires_at > time.time() + TOKEN_REFRESH_SKEW_SECONDS
    )


def _require_client_credentials_settings(settings: LedgerAuthSettings) -> None:
    missing = []
    if not settings.token_url:
        missing.append(LEDGER_API_TOKEN_URL)
    if not settings.client_id:
        missing.append(LEDGER_API_CLIENT_ID)
    if not settings.client_secret:
        missing.append(LEDGER_API_CLIENT_SECRET)

    if missing:
        names = ", ".join(missing)
        raise ConfigurationError(f"Missing Ledger API OAuth setting(s): {names}.")


def _request_client_credentials_token(settings: LedgerAuthSettings) -> str:
    global _TOKEN_CACHE

    _require_client_credentials_settings(settings)
    if _cached_token_is_valid(settings):
        return _TOKEN_CACHE.access_token

    data = {
        "grant_type": "client_credentials",
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "scope": settings.token_scope,
    }
    if settings.audience:
        data["audience"] = settings.audience

    try:
        response = requests.post(
            settings.token_url,
            data=data,
            timeout=settings.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise LedgerSubmissionError("Ledger API token request failed due to a network error.") from exc

    if response.status_code >= 400:
        raise LedgerSubmissionError(
            f"Ledger API token request failed with HTTP {response.status_code}."
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise LedgerSubmissionError("Ledger API token response was not valid JSON.") from exc

    access_token = body.get("access_token")
    if not access_token:
        raise LedgerSubmissionError("Ledger API token response did not include an access token.")

    try:
        expires_in = int(body.get("expires_in", 300))
    except (TypeError, ValueError):
        expires_in = 300

    _TOKEN_CACHE = CachedBearerToken(
        access_token=access_token,
        expires_at=time.time() + max(expires_in, TOKEN_REFRESH_SKEW_SECONDS),
        cache_key=_client_credentials_cache_key(settings),
    )
    return access_token


def get_bearer_token(settings: LedgerAuthSettings) -> str:
    if settings.auth_token:
        return settings.auth_token

    return _request_client_credentials_token(settings)


def build_auth_headers(settings: LedgerAuthSettings) -> dict[str, str]:
    token = get_bearer_token(settings)
    return {"Authorization": f"Bearer {token}"}
