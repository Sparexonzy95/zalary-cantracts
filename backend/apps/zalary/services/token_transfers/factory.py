import os

from apps.zalary.services.auth import env_flag_enabled

from .base import TokenTransferProvider, UnavailableTokenTransferProvider
from .usdcx import ConfiguredUSDCxTransferProvider
from .zusd import ConfiguredZUSDTransferProvider, ZALARY_TEST_TOKEN_PROVIDER_MODE


TOKEN_TRANSFER_PROVIDER = "ZALARY_TOKEN_TRANSFER_PROVIDER"
USDCX_TRANSFER_PROVIDER = "ZALARY_USDCX_TRANSFER_PROVIDER"
USDCX_UTILITY_API_URL = "ZALARY_USDCX_UTILITY_API_URL"
USDCX_XRESERVE_API_URL = "ZALARY_USDCX_XRESERVE_API_URL"
USDCX_ALLOW_EXTERNAL_PROOF = "ZALARY_USDCX_ALLOW_EXTERNAL_PROOF"
USDCX_TRANSFER_TIMEOUT_SECONDS = "ZALARY_USDCX_TRANSFER_TIMEOUT_SECONDS"
USDCX_HOLDING_INTERFACE_ID = "ZALARY_USDCX_HOLDING_INTERFACE_ID"
USDCX_TRANSFER_FACTORY_INTERFACE_ID = "ZALARY_USDCX_TRANSFER_FACTORY_INTERFACE_ID"
USDCX_TRANSFER_INSTRUCTION_INTERFACE_ID = "ZALARY_USDCX_TRANSFER_INSTRUCTION_INTERFACE_ID"
USDCX_TRANSFER_FACTORY_ENDPOINT = "ZALARY_USDCX_TRANSFER_FACTORY_ENDPOINT"
USDCX_AUTO_ACCEPT_PENDING_TRANSFER = "ZALARY_USDCX_AUTO_ACCEPT_PENDING_TRANSFER"
USDCX_ALLOW_CANONICAL_TRANSFER_ARGUMENT = "ZALARY_USDCX_ALLOW_CANONICAL_TRANSFER_ARGUMENT"
USDCX_TRANSFER_ARGUMENT_SHAPE = "ZALARY_USDCX_TRANSFER_ARGUMENT_SHAPE"
ENABLE_DEMO_SETTLEMENT_PROOF = "ZALARY_ENABLE_DEMO_SETTLEMENT_PROOF"


def get_token_transfer_provider() -> TokenTransferProvider:
    provider = _clean(os.environ.get(TOKEN_TRANSFER_PROVIDER)).lower()
    usdcx_mode = _clean(os.environ.get(USDCX_TRANSFER_PROVIDER))
    if not provider and not usdcx_mode:
        return UnavailableTokenTransferProvider()
    if provider == ZALARY_TEST_TOKEN_PROVIDER_MODE:
        return ConfiguredZUSDTransferProvider()
    if provider in {"usdcx", "usdcx_transfer"} or usdcx_mode:
        return ConfiguredUSDCxTransferProvider(
            utility_api_url=_clean(os.environ.get(USDCX_UTILITY_API_URL)),
            xreserve_api_url=_clean(os.environ.get(USDCX_XRESERVE_API_URL)),
            provider_mode=usdcx_mode,
            timeout_seconds=_timeout_seconds(),
            holding_interface_id=_clean(os.environ.get(USDCX_HOLDING_INTERFACE_ID)),
            transfer_factory_interface_id=_clean(os.environ.get(USDCX_TRANSFER_FACTORY_INTERFACE_ID)),
            transfer_instruction_interface_id=_clean(os.environ.get(USDCX_TRANSFER_INSTRUCTION_INTERFACE_ID)),
            transfer_factory_endpoint=_clean(os.environ.get(USDCX_TRANSFER_FACTORY_ENDPOINT)),
            auto_accept_pending_transfer=env_flag_enabled(USDCX_AUTO_ACCEPT_PENDING_TRANSFER, default=False),
            allow_canonical_transfer_argument=env_flag_enabled(
                USDCX_ALLOW_CANONICAL_TRANSFER_ARGUMENT,
                default=False,
            ),
            transfer_argument_shape=_clean(os.environ.get(USDCX_TRANSFER_ARGUMENT_SHAPE)),
        )
    return UnavailableTokenTransferProvider()


def external_proof_enabled() -> bool:
    return env_flag_enabled(USDCX_ALLOW_EXTERNAL_PROOF, default=False)


def demo_settlement_proof_enabled() -> bool:
    return env_flag_enabled(ENABLE_DEMO_SETTLEMENT_PROOF, default=False)


def _timeout_seconds() -> int:
    value = _clean(os.environ.get(USDCX_TRANSFER_TIMEOUT_SECONDS)) or "60"
    try:
        parsed = int(value)
    except ValueError:
        return 60
    return max(parsed, 1)


def _clean(value: str | None) -> str:
    return (value or "").strip()
