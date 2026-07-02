from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Sequence
from urllib.parse import urljoin
import uuid

from django.utils import timezone
import requests

from apps.zalary.services.auth import default_read_parties, load_ledger_auth_settings
from apps.zalary.services.errors import LedgerSubmissionError, LedgerSyncError, safe_error_message
from apps.zalary.services.ledger import (
    CommandContext,
    LedgerClient,
    LedgerCommandResult,
    normalize_active_contracts_response,
    template_display_text,
)
from apps.zalary.services.payloads import decimal_to_daml

from .base import (
    BaseTokenTransferProvider,
    TokenTransferRequest,
    TokenTransferResult,
    TRANSFER_COMPLETED,
    TRANSFER_FAILED,
    TRANSFER_PENDING,
    TRANSFER_PENDING_RECEIVER_ACCEPTANCE,
    TRANSFER_UNAVAILABLE,
)


USDCX_UNCONFIGURED_ERROR = "USDCx transfer provider is not configured; real settlement cannot be confirmed."
USDCX_INSUFFICIENT_HOLDINGS_ERROR = "Insufficient USDCx holdings for employer wallet."
USDCX_EXTERNAL_SIGNING_ERROR = "External party transfer signing is required; prepare/execute flow is not implemented."

DEFAULT_HOLDING_INTERFACE_ID = "#splice-api-token-holding-v1:Splice.Api.Token.HoldingV1:Holding"
DEFAULT_TRANSFER_FACTORY_INTERFACE_ID = (
    "#splice-api-token-transfer-instruction-v1:Splice.Api.Token.TransferInstructionV1:TransferFactory"
)
DEFAULT_TRANSFER_INSTRUCTION_INTERFACE_ID = (
    "#splice-api-token-transfer-instruction-v1:Splice.Api.Token.TransferInstructionV1:TransferInstruction"
)
TRANSFER_FACTORY_CHOICE = "TransferFactory_Transfer"
DEFAULT_TRANSFER_FACTORY_ENDPOINT = (
    "https://api.utilities.digitalasset-staging.com/registry/transfer-instruction/v1/transfer-factory"
)
TRANSFER_KIND_SELF = "self"
TRANSFER_KIND_DIRECT = "direct"
TRANSFER_KIND_OFFER = "offer"
TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS = "transfer_extra_args"
TRANSFER_ARGUMENT_SHAPE_CANONICAL_FLAT = "canonical_flat"
SUPPORTED_TRANSFER_ARGUMENT_SHAPES = {
    TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS,
    TRANSFER_ARGUMENT_SHAPE_CANONICAL_FLAT,
}
P2PLENDING_PROVIDER_MODE = "p2plending_custom"
P2PLENDING_USDCX_PACKAGE_ID = "f2471aac14f6a1a499dcb9362b8a206836bd1aff17be4a367d6a9b0255b75a29"
P2PLENDING_USDCX_MODULE = "P2PLending.Token.USDCx"
P2PLENDING_USDCX_HOLDING_TEMPLATE = "USDCxHolding"
P2PLENDING_USDCX_REGISTRY_TEMPLATE = "USDCxRegistry"
P2PLENDING_TRANSFER_CHOICE = "USDCx_TransferByOwner"
P2PLENDING_DIRECT_TRANSFER_CHOICE = "USDCx_Transfer"
P2PLENDING_SPLIT_CHOICE = "USDCxHolding_Split"
P2PLENDING_MERGE_CHOICE = "USDCxHolding_Merge"
DEFAULT_USDCX_INSTRUMENT_ADMIN = (
    "decentralized-usdc-interchain-rep::122049e2af8a725bd19759320fc83c638e7718973eac189d8f201309c512d1ffec61"
)
P2PLENDING_SCHEMA = {
    P2PLENDING_TRANSFER_CHOICE: {
        "template": f"{P2PLENDING_USDCX_PACKAGE_ID}:{P2PLENDING_USDCX_MODULE}:{P2PLENDING_USDCX_REGISTRY_TEMPLATE}",
        "contract_type": P2PLENDING_USDCX_REGISTRY_TEMPLATE,
        "act_as": "sender",
        "consuming": True,
        "argument_shape": {
            "sender": "Party",
            "holdingCid": "ContractId USDCxHolding",
            "newOwner": "Party",
        },
        "return_type": "ContractId USDCxHolding",
    },
    P2PLENDING_DIRECT_TRANSFER_CHOICE: {
        "template": f"{P2PLENDING_USDCX_PACKAGE_ID}:{P2PLENDING_USDCX_MODULE}:{P2PLENDING_USDCX_REGISTRY_TEMPLATE}",
        "contract_type": P2PLENDING_USDCX_REGISTRY_TEMPLATE,
        "act_as": "holding owner",
        "consuming": True,
        "argument_shape": {
            "holdingCid": "ContractId USDCxHolding",
            "newOwner": "Party",
        },
        "return_type": "ContractId USDCxHolding",
    },
    P2PLENDING_SPLIT_CHOICE: {
        "template": f"{P2PLENDING_USDCX_PACKAGE_ID}:{P2PLENDING_USDCX_MODULE}:{P2PLENDING_USDCX_HOLDING_TEMPLATE}",
        "contract_type": P2PLENDING_USDCX_HOLDING_TEMPLATE,
        "act_as": "actor",
        "consuming": True,
        "argument_shape": {
            "actor": "Party",
            "splitAmount": "Decimal",
        },
        "return_type": "(ContractId USDCxHolding, ContractId USDCxHolding)",
    },
    P2PLENDING_MERGE_CHOICE: {
        "template": f"{P2PLENDING_USDCX_PACKAGE_ID}:{P2PLENDING_USDCX_MODULE}:{P2PLENDING_USDCX_HOLDING_TEMPLATE}",
        "contract_type": P2PLENDING_USDCX_HOLDING_TEMPLATE,
        "act_as": "actor",
        "consuming": True,
        "argument_shape": {
            "actor": "Party",
            "otherCid": "ContractId USDCxHolding",
        },
        "return_type": "ContractId USDCxHolding",
    },
}


@dataclass(frozen=True)
class HoldingCandidate:
    contract_id: str
    amount: Decimal
    owner: str
    instrument: dict[str, Any]
    interface_view: dict[str, Any] = field(default_factory=dict)
    created_event_blob: str = ""
    payload_keys: list[str] = field(default_factory=list)
    locked: bool = False

    def safe_summary(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "amount": decimal_to_daml(self.amount),
            "owner": self.owner,
            "instrument": self.instrument,
            "locked": self.locked,
            "payload_keys": self.payload_keys,
            "has_created_event_blob": bool(self.created_event_blob),
        }


@dataclass(frozen=True)
class TransferFactoryDiscovery:
    factory_id: str = ""
    transfer_kind: str = ""
    disclosed_contracts: list[dict[str, Any]] = field(default_factory=list)
    choice_context_data: Any = None
    choice_context_present: bool = False
    choice_argument: dict[str, Any] | None = None
    preliminary_choice_argument: dict[str, Any] = field(default_factory=dict)
    final_choice_argument: dict[str, Any] = field(default_factory=dict)
    argument_shape: str = ""
    attempted_argument_shapes: list[str] = field(default_factory=list)
    rejected_argument_shapes: list[dict[str, str]] = field(default_factory=list)
    external_party_signing_required: bool = False
    endpoints_tested: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    raw_provider_reference: str = ""

    @property
    def schema_confirmed(self) -> bool:
        return bool(self.factory_id and self.final_choice_argument)

    def safe_summary(self) -> dict[str, Any]:
        can_build_final_choice_argument = bool(self.final_choice_argument)
        can_submit_live_transfer = bool(self.factory_id and can_build_final_choice_argument and not self.blockers)
        return {
            "factory_id": self.factory_id,
            "factoryId_present": bool(self.factory_id),
            "transferKind": self.transfer_kind,
            "choiceContext_present": self.choice_context_present,
            "choiceContextData_present": self.choice_context_data is not None,
            "disclosed_contract_count": len(self.disclosed_contracts),
            "disclosedContracts_count": len(self.disclosed_contracts),
            "choice_argument_present": self.choice_argument is not None,
            "argument_shape": self.argument_shape,
            "attempted_argument_shapes": self.attempted_argument_shapes,
            "rejected_argument_shapes": self.rejected_argument_shapes,
            "can_build_final_choice_argument": can_build_final_choice_argument,
            "can_submit_live_transfer": can_submit_live_transfer,
            "external_party_signing_required": self.external_party_signing_required,
            "endpoints_tested": self.endpoints_tested,
            "blockers": self.blockers,
            "raw_provider_reference": self.raw_provider_reference,
        }


@dataclass(frozen=True)
class TransferCommandPlan:
    ready: bool
    command_payload: dict[str, Any]
    choice_argument: dict[str, Any]
    disclosed_contracts: list[dict[str, Any]]
    selected_holdings: list[HoldingCandidate]
    transfer_kind: str = ""
    blockers: list[str] = field(default_factory=list)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "choice": TRANSFER_FACTORY_CHOICE,
            "transferKind": self.transfer_kind,
            "contract_id": self.command_payload.get("contractId") or "",
            "template_id": self.command_payload.get("templateId") or "",
            "selected_holdings": [holding.safe_summary() for holding in self.selected_holdings],
            "selected_holding_count": len(self.selected_holdings),
            "selected_holding_total": decimal_to_daml(sum((h.amount for h in self.selected_holdings), Decimal("0"))),
            "disclosed_contract_count": len(self.disclosed_contracts),
            "choice_argument_keys": sorted(self.choice_argument.keys()),
            "blockers": self.blockers,
        }


@dataclass(frozen=True)
class P2PLendingTransferPlan:
    ready: bool
    selected_holding: HoldingCandidate | None = None
    exact_amount_holding: HoldingCandidate | None = None
    registry_contract: dict[str, Any] | None = None
    split_required: bool = False
    transfer_choice: str = P2PLENDING_TRANSFER_CHOICE
    split_choice: str = P2PLENDING_SPLIT_CHOICE
    act_as: str = ""
    transfer_argument: dict[str, Any] = field(default_factory=dict)
    split_argument: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    schema: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        registry_template = ""
        registry_contract_id = ""
        if self.registry_contract:
            registry_template = template_display_text(self.registry_contract.get("template_id") or {})
            registry_contract_id = str(self.registry_contract.get("contract_id") or "")
        return {
            "ready": self.ready,
            "selected_holding_cid": _truncate_contract_id(self.selected_holding.contract_id if self.selected_holding else ""),
            "selected_holding_amount": (
                decimal_to_daml(self.selected_holding.amount) if self.selected_holding else ""
            ),
            "exact_amount_holding_available": bool(self.exact_amount_holding),
            "split_required": self.split_required,
            "registry_contract_found": bool(self.registry_contract),
            "registry_contract_id": _truncate_contract_id(registry_contract_id),
            "registry_contract_template": registry_template,
            "transfer_choice_selected": self.transfer_choice,
            "split_choice_selected": self.split_choice if self.split_required else "",
            "act_as": self.act_as,
            "transfer_argument_shape": _sanitize_choice_argument(self.transfer_argument),
            "split_argument_shape": _sanitize_choice_argument(self.split_argument),
            "schema": self.schema,
            "can_submit": self.ready,
            "blockers": self.blockers,
        }


class USDCxTransferProvider(BaseTokenTransferProvider):
    provider_name = "usdcx"

    def execute_transfer(self, request: TokenTransferRequest) -> TokenTransferResult:
        return TokenTransferResult(
            status=TRANSFER_UNAVAILABLE,
            token=request.token,
            sender=request.sender_party,
            receiver=request.receiver_party,
            amount=request.amount,
            transferReference=request.transfer_reference,
            provider_name=self.provider_name,
            error_message=USDCX_UNCONFIGURED_ERROR,
        )


@dataclass(frozen=True)
class ConfiguredUSDCxTransferProvider(USDCxTransferProvider):
    utility_api_url: str = ""
    xreserve_api_url: str = ""
    provider_mode: str = ""
    timeout_seconds: int = 60
    holding_interface_id: str = ""
    transfer_factory_interface_id: str = ""
    transfer_instruction_interface_id: str = ""
    transfer_factory_endpoint: str = ""
    auto_accept_pending_transfer: bool = False
    allow_canonical_transfer_argument: bool = False
    transfer_argument_shape: str = ""

    def __post_init__(self):
        object.__setattr__(
            self,
            "holding_interface_id",
            self.holding_interface_id or DEFAULT_HOLDING_INTERFACE_ID,
        )
        object.__setattr__(
            self,
            "transfer_factory_interface_id",
            self.transfer_factory_interface_id or DEFAULT_TRANSFER_FACTORY_INTERFACE_ID,
        )
        object.__setattr__(
            self,
            "transfer_instruction_interface_id",
            self.transfer_instruction_interface_id or DEFAULT_TRANSFER_INSTRUCTION_INTERFACE_ID,
        )
        object.__setattr__(
            self,
            "transfer_factory_endpoint",
            self.transfer_factory_endpoint or DEFAULT_TRANSFER_FACTORY_ENDPOINT,
        )
        shape = self.transfer_argument_shape or TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS
        if shape not in SUPPORTED_TRANSFER_ARGUMENT_SHAPES:
            shape = TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS
        object.__setattr__(self, "transfer_argument_shape", shape)

    def execute_transfer(self, request: TokenTransferRequest) -> TokenTransferResult:
        if self.provider_mode == P2PLENDING_PROVIDER_MODE:
            return self._execute_p2plending_transfer(request)

        if self.provider_mode != "token_standard":
            return TokenTransferResult(
                status=TRANSFER_UNAVAILABLE,
                token=request.token,
                sender=request.sender_party,
                receiver=request.receiver_party,
                amount=request.amount,
                transferReference=request.transfer_reference,
                provider_name=self.provider_name,
                error_message=USDCX_UNCONFIGURED_ERROR,
            )

        try:
            holdings = self.list_usdcx_holdings(request)
            selected_holdings = self.select_input_holdings(holdings, request.amount)
            discovery = self.discover_transfer_factory(request, selected_holdings=selected_holdings)
            plan = self.build_transfer_command_plan(
                request=request,
                selected_holdings=selected_holdings,
                discovery=discovery,
            )
        except InsufficientHoldingsError as exc:
            return self._result(
                request,
                status=TRANSFER_FAILED,
                error_message=str(exc),
            )
        except (LedgerSyncError, LedgerSubmissionError, requests.RequestException, ValueError) as exc:
            return self._result(
                request,
                status=TRANSFER_UNAVAILABLE,
                error_message=safe_error_message(exc),
            )

        if discovery.external_party_signing_required:
            return self._result(
                request,
                status=TRANSFER_UNAVAILABLE,
                error_message=USDCX_EXTERNAL_SIGNING_ERROR,
                raw_provider_reference=discovery.raw_provider_reference,
            )
        if discovery.transfer_kind not in {TRANSFER_KIND_SELF, TRANSFER_KIND_DIRECT, TRANSFER_KIND_OFFER}:
            return self._result(
                request,
                status=TRANSFER_UNAVAILABLE,
                error_message="TransferFactory returned an unknown transferKind; live transfer is blocked.",
                raw_provider_reference=discovery.raw_provider_reference,
            )
        if discovery.transfer_kind == TRANSFER_KIND_OFFER and not _allow_pending_offer(request):
            return self._result(
                request,
                status=TRANSFER_PENDING_RECEIVER_ACCEPTANCE,
                error_message="TransferFactory returned transferKind=offer; submit only with --allow-pending.",
                raw_provider_reference=discovery.raw_provider_reference,
            )
        if not plan.ready:
            return self._result(
                request,
                status=TRANSFER_UNAVAILABLE,
                error_message="; ".join(plan.blockers) or "USDCx transfer command schema is not ready.",
                raw_provider_reference=discovery.raw_provider_reference,
            )

        try:
            command_result = self._submit_transfer_command(request=request, plan=plan)
        except LedgerSubmissionError as exc:
            return self._result(
                request,
                status=TRANSFER_FAILED,
                error_message=safe_error_message(exc),
                raw_provider_reference=discovery.raw_provider_reference,
            )

        result = self.parse_transfer_result(command_result.raw_response, request=request)
        if result.status == TRANSFER_PENDING and self.auto_accept_pending_transfer:
            return self.accept_transfer_instruction(request=request, pending_result=result)
        return result

    def list_usdcx_holdings(self, request: TokenTransferRequest) -> list[HoldingCandidate]:
        settings = load_ledger_auth_settings()
        client = LedgerClient(settings)
        parties = _dedupe([request.sender_party, *default_read_parties()])
        contracts = client.query_active_contracts_by_interface(
            interface_id=self.holding_interface_id,
            parties=parties,
        )
        candidates = []
        for contract in contracts:
            candidate = self._holding_candidate_from_contract(contract)
            if candidate is None:
                continue
            if candidate.owner != request.sender_party:
                continue
            if not _instrument_matches(candidate.instrument, request.token):
                continue
            if candidate.amount <= Decimal("0"):
                continue
            if candidate.locked:
                continue
            candidates.append(candidate)
        return sorted(candidates, key=lambda item: (item.amount, item.contract_id))

    def select_input_holdings(
        self,
        holdings: Sequence[HoldingCandidate],
        required_amount: str | Decimal,
    ) -> list[HoldingCandidate]:
        required = _decimal(required_amount)
        if required <= Decimal("0"):
            raise InsufficientHoldingsError(USDCX_INSUFFICIENT_HOLDINGS_ERROR)
        available = sorted(holdings, key=lambda item: (item.amount, item.contract_id))
        if sum((holding.amount for holding in available), Decimal("0")) < required:
            raise InsufficientHoldingsError(USDCX_INSUFFICIENT_HOLDINGS_ERROR)

        if len(available) <= 18:
            best: tuple[Decimal, tuple[str, ...], tuple[HoldingCandidate, ...]] | None = None
            for size in range(1, len(available) + 1):
                size_matches = []
                for combo in combinations(available, size):
                    total = sum((holding.amount for holding in combo), Decimal("0"))
                    if total >= required:
                        size_matches.append((total, tuple(holding.contract_id for holding in combo), combo))
                if size_matches:
                    best = sorted(size_matches, key=lambda item: (item[0], item[1]))[0]
                    break
            if best is not None:
                return list(best[2])

        selected = []
        total = Decimal("0")
        for holding in sorted(available, key=lambda item: (-item.amount, item.contract_id)):
            selected.append(holding)
            total += holding.amount
            if total >= required:
                return sorted(selected, key=lambda item: item.contract_id)
        raise InsufficientHoldingsError(USDCX_INSUFFICIENT_HOLDINGS_ERROR)

    def discover_transfer_factory(
        self,
        request: TokenTransferRequest,
        *,
        selected_holdings: Sequence[HoldingCandidate],
    ) -> TransferFactoryDiscovery:
        endpoints_tested = []
        blockers = []
        if not self.transfer_factory_endpoint:
            blockers.append(
                "TransferFactory registry endpoint is not configured; set ZALARY_USDCX_TRANSFER_FACTORY_ENDPOINT "
                "to the Utility API transfer factory registry endpoint."
            )
            return TransferFactoryDiscovery(endpoints_tested=endpoints_tested, blockers=blockers)

        endpoint = self._resolved_factory_endpoint()
        endpoints_tested.append(_safe_url_label(endpoint))

        attempted_shapes = _argument_shape_attempt_order(
            self.transfer_argument_shape,
            allow_canonical=self.allow_canonical_transfer_argument,
        )
        rejected_shapes: list[dict[str, str]] = []
        response_body: dict[str, Any] | None = None
        chosen_shape = ""
        preliminary_choice_argument: dict[str, Any] = {}
        last_error = ""

        for shape in attempted_shapes:
            preliminary_choice_argument = self.build_preliminary_choice_argument(
                request=request,
                selected_holdings=selected_holdings,
                shape=shape,
            )
            try:
                response = requests.post(
                    endpoint,
                    json={
                        "choiceArguments": preliminary_choice_argument,
                        "excludeDebugFields": True,
                    },
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException:
                last_error = "TransferFactory discovery failed due to a network error."
                rejected_shapes.append({"shape": shape, "error": "network_error"})
                continue

            if response.status_code >= 400:
                safe_detail = _safe_response_text(response.text)
                last_error = f"TransferFactory discovery failed with HTTP {response.status_code}: {safe_detail}"
                rejected_shapes.append({"shape": shape, "error": last_error})
                continue

            try:
                parsed_body = response.json()
            except ValueError:
                last_error = "TransferFactory discovery response was not valid JSON."
                rejected_shapes.append({"shape": shape, "error": "invalid_json"})
                continue

            if not isinstance(parsed_body, dict):
                last_error = "TransferFactory discovery response was not a JSON object."
                rejected_shapes.append({"shape": shape, "error": "invalid_json_shape"})
                continue

            response_body = parsed_body
            chosen_shape = shape
            break

        if response_body is None:
            blockers.append(last_error or "TransferFactory discovery failed for all argument shapes.")
            return TransferFactoryDiscovery(
                preliminary_choice_argument=preliminary_choice_argument,
                argument_shape="",
                attempted_argument_shapes=attempted_shapes,
                rejected_argument_shapes=rejected_shapes,
                endpoints_tested=endpoints_tested,
                blockers=blockers,
            )

        body = response_body

        factory_id = _find_first_string(
            body,
            ("factoryId", "factory_id", "transferFactoryCid", "transferFactoryId", "contractId", "contract_id"),
        )
        transfer_kind = str(body.get("transferKind") or body.get("transfer_kind") or "").strip().lower()
        choice_context = body.get("choiceContext") or body.get("choice_context") or {}
        choice_context_present = isinstance(choice_context, dict) and bool(choice_context)
        disclosed_contracts = []
        if isinstance(choice_context, dict):
            disclosed_contracts = choice_context.get("disclosedContracts") or choice_context.get("disclosed_contracts") or []
        if not disclosed_contracts:
            disclosed_contracts = _find_first_list(body, ("disclosedContracts", "disclosed_contracts")) or []
        choice_context_data = None
        if isinstance(choice_context, dict):
            choice_context_data = choice_context.get("choiceContextData") or choice_context.get("choice_context_data")
        if choice_context_data is None:
            choice_context_data = _find_first_value(body, ("choiceContextData", "choice_context_data"))
        choice_argument = _find_first_dict(
            body,
            ("choiceArgument", "choice_argument", "transferChoiceArgument", "transfer_choice_argument"),
        )
        external_signing = bool(
            _find_first_value(
                body,
                (
                    "externalPartySigningRequired",
                    "external_party_signing_required",
                    "requiresExternalSigning",
                    "requires_external_signing",
                ),
            )
        )
        raw_provider_reference = _find_first_string(
            body,
            ("reference", "requestId", "request_id", "factoryReference", "factory_reference"),
        ) or ""

        if not factory_id:
            blockers.append("TransferFactory discovery response did not include factoryId.")
        if transfer_kind not in {TRANSFER_KIND_SELF, TRANSFER_KIND_DIRECT, TRANSFER_KIND_OFFER}:
            blockers.append("TransferFactory discovery response included an unknown transferKind.")
        if choice_context_data is None:
            blockers.append("TransferFactory discovery response did not include choiceContext.choiceContextData.")

        final_choice_argument = {}
        if choice_context_data is not None:
            final_choice_argument = self.build_final_choice_argument(
                request=request,
                selected_holdings=selected_holdings,
                choice_context_data=choice_context_data,
                shape=chosen_shape,
            )
        elif choice_argument is not None:
            final_choice_argument = choice_argument

        return TransferFactoryDiscovery(
            factory_id=factory_id or "",
            transfer_kind=transfer_kind,
            disclosed_contracts=[item for item in disclosed_contracts if isinstance(item, dict)],
            choice_context_data=choice_context_data,
            choice_context_present=choice_context_present,
            choice_argument=choice_argument,
            preliminary_choice_argument=preliminary_choice_argument,
            final_choice_argument=final_choice_argument,
            argument_shape=chosen_shape,
            attempted_argument_shapes=attempted_shapes,
            rejected_argument_shapes=rejected_shapes,
            external_party_signing_required=external_signing,
            endpoints_tested=endpoints_tested,
            blockers=blockers,
            raw_provider_reference=raw_provider_reference,
        )

    def build_transfer_command_plan(
        self,
        *,
        request: TokenTransferRequest,
        selected_holdings: Sequence[HoldingCandidate],
        discovery: TransferFactoryDiscovery,
    ) -> TransferCommandPlan:
        blockers = list(discovery.blockers)
        if not discovery.factory_id:
            blockers.append("TransferFactory contract id is required before transfer command submission.")
        if discovery.external_party_signing_required:
            blockers.append(USDCX_EXTERNAL_SIGNING_ERROR)
        if discovery.transfer_kind not in {TRANSFER_KIND_SELF, TRANSFER_KIND_DIRECT, TRANSFER_KIND_OFFER}:
            blockers.append("TransferFactory transferKind must be self, direct, or offer before submission.")

        choice_argument = discovery.final_choice_argument or discovery.choice_argument or {}
        if not choice_argument:
            blockers.append(
                "TransferFactory_Transfer final choiceArgument could not be built from registry choiceContext."
            )

        command_payload = {
            "templateId": self.transfer_factory_interface_id,
            "contractId": discovery.factory_id,
            "choice": TRANSFER_FACTORY_CHOICE,
            "choiceArgument": choice_argument,
        }
        return TransferCommandPlan(
            ready=not blockers,
            command_payload=command_payload,
            choice_argument=choice_argument,
            disclosed_contracts=list(discovery.disclosed_contracts),
            selected_holdings=list(selected_holdings),
            transfer_kind=discovery.transfer_kind,
            blockers=blockers,
        )

    def parse_transfer_result(
        self,
        response: dict[str, Any] | list[Any] | LedgerCommandResult,
        *,
        request: TokenTransferRequest,
    ) -> TokenTransferResult:
        body: Any = response.raw_response if isinstance(response, LedgerCommandResult) else response
        status_text = (
            _find_first_string(body, ("status", "transferStatus", "transfer_status", "resultStatus"))
            or _find_first_variant_tag(body)
            or ""
        ).lower()
        transfer_instruction_cid = _find_first_string(
            body,
            (
                "transferInstructionCid",
                "transfer_instruction_cid",
                "transferInstructionContractId",
                "transferInstructionId",
            ),
        )
        if not transfer_instruction_cid:
            transfer_instruction_cid = _find_created_contract_id(body, contains="TransferInstruction")
        holding_cid = _find_first_string(
            body,
            (
                "receiverHoldingCid",
                "receiver_holding_cid",
                "holdingCid",
                "holding_cid",
                "holdingContractId",
                "outputHoldingCid",
                "newHoldingCid",
            ),
        )
        if not holding_cid:
            holding_cid = _find_created_contract_id(body, contains="Holding")
        update_id = _find_first_string(body, ("updateId", "update_id", "transactionId", "transaction_id")) or ""
        executed_at = (
            _find_first_string(body, ("executedAt", "executed_at", "completionTime", "recordTime", "effectiveAt"))
            or timezone.now().isoformat().replace("+00:00", "Z")
        )
        error_message = _find_first_string(body, ("error", "errorMessage", "error_message", "reason")) or ""

        if "completed" in status_text or ("success" in status_text and holding_cid) or holding_cid:
            return self._result(
                request,
                status=TRANSFER_COMPLETED,
                transfer_instruction_cid=transfer_instruction_cid,
                holding_cid=holding_cid,
                executed_at=executed_at,
                raw_provider_reference=update_id,
            )
        if "acceptance" in status_text:
            return self._result(
                request,
                status=TRANSFER_PENDING_RECEIVER_ACCEPTANCE,
                transfer_instruction_cid=transfer_instruction_cid,
                raw_provider_reference=update_id,
            )
        if "pending" in status_text or transfer_instruction_cid:
            return self._result(
                request,
                status=TRANSFER_PENDING,
                transfer_instruction_cid=transfer_instruction_cid,
                raw_provider_reference=update_id,
            )
        if "failed" in status_text or "reject" in status_text or error_message:
            return self._result(
                request,
                status=TRANSFER_FAILED,
                transfer_instruction_cid=transfer_instruction_cid,
                raw_provider_reference=update_id,
                error_message=error_message or "USDCx transfer failed.",
            )
        return self._result(
            request,
            status=TRANSFER_FAILED,
            raw_provider_reference=update_id,
            error_message="USDCx transfer result was not recognized as completed or pending.",
        )

    def accept_transfer_instruction(
        self,
        *,
        request: TokenTransferRequest,
        pending_result: TokenTransferResult,
    ) -> TokenTransferResult:
        return self._result(
            request,
            status=TRANSFER_PENDING,
            transfer_instruction_cid=pending_result.transferInstructionCid,
            raw_provider_reference=pending_result.raw_provider_reference,
            error_message=(
                "Pending transfer auto-accept is enabled, but receiver-party signing details are not configured."
            ),
        )

    def diagnostic_summary(self, request: TokenTransferRequest, *, max_holdings: int = 10) -> dict[str, Any]:
        holdings = self.list_usdcx_holdings(request)
        selected = []
        selection_error = ""
        try:
            selected = self.select_input_holdings(holdings, request.amount)
        except InsufficientHoldingsError as exc:
            selection_error = str(exc)
        if selected:
            discovery = self.discover_transfer_factory(request, selected_holdings=selected)
            plan = self.build_transfer_command_plan(
                request=request,
                selected_holdings=selected,
                discovery=discovery,
            )
        else:
            discovery = TransferFactoryDiscovery(blockers=[selection_error] if selection_error else [])
            plan = None
        return {
            "provider": self.safe_config(),
            "matching_holding_count": len(holdings),
            "holdings": [holding.safe_summary() for holding in holdings[:max_holdings]],
            "selected_holdings": [holding.safe_summary() for holding in selected],
            "selection_error": selection_error,
            "transfer_factory": discovery.safe_summary(),
            "transfer_command_plan": plan.safe_summary() if plan is not None else None,
            "can_submit_transfer": bool(plan and plan.ready),
        }

    def _submit_transfer_command(
        self,
        *,
        request: TokenTransferRequest,
        plan: TransferCommandPlan,
    ) -> LedgerCommandResult:
        settings = load_ledger_auth_settings()
        client = LedgerClient(settings)
        context = CommandContext(
            act_as=[request.sender_party],
            read_as=_dedupe(default_read_parties()),
            command_id=f"zalary-usdcx-transfer-{uuid.uuid4().hex}",
            workflow_id=f"zalary-usdcx-transfer-{request.transfer_reference}",
        )
        return client.submit_exercise_interface(
            context=context,
            interface_id=self.transfer_factory_interface_id,
            contract_id=plan.command_payload["contractId"],
            choice=TRANSFER_FACTORY_CHOICE,
            argument=plan.choice_argument,
            disclosed_contracts=plan.disclosed_contracts,
        )

    def build_preliminary_choice_argument(
        self,
        *,
        request: TokenTransferRequest,
        selected_holdings: Sequence[HoldingCandidate],
        shape: str | None = None,
    ) -> dict[str, Any]:
        return self._choice_argument_for_shape(
            request=request,
            selected_holdings=selected_holdings,
            choice_context_data={},
            meta={},
            shape=shape or self.transfer_argument_shape,
        )

    def build_final_choice_argument(
        self,
        *,
        request: TokenTransferRequest,
        selected_holdings: Sequence[HoldingCandidate],
        choice_context_data: Any,
        shape: str | None = None,
    ) -> dict[str, Any]:
        return self._choice_argument_for_shape(
            request=request,
            selected_holdings=selected_holdings,
            choice_context_data=choice_context_data if choice_context_data is not None else {},
            meta={"zalarySettlementReference": request.transfer_reference},
            shape=shape or self.transfer_argument_shape,
        )

    def _choice_argument_for_shape(
        self,
        *,
        request: TokenTransferRequest,
        selected_holdings: Sequence[HoldingCandidate],
        choice_context_data: Any,
        meta: dict[str, Any],
        shape: str,
    ) -> dict[str, Any]:
        normalized_shape = shape if shape in SUPPORTED_TRANSFER_ARGUMENT_SHAPES else TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS
        transfer = self._transfer_details(request=request, selected_holdings=selected_holdings)
        if normalized_shape == TRANSFER_ARGUMENT_SHAPE_CANONICAL_FLAT:
            return {
                **transfer,
                "choiceContextData": choice_context_data,
                "metadata": dict(meta),
            }
        return {
            "transfer": transfer,
            "extraArgs": {
                "context": choice_context_data,
                "meta": dict(meta),
            },
        }

    def _transfer_details(
        self,
        *,
        request: TokenTransferRequest,
        selected_holdings: Sequence[HoldingCandidate],
    ) -> dict[str, Any]:
        execute_before = timezone.now() + timedelta(seconds=max(self.timeout_seconds, 1))
        return {
            "sender": request.sender_party,
            "receiver": request.receiver_party,
            "amount": decimal_to_daml(request.amount),
            "instrument": {
                "id": _instrument_id(request.token),
                "admin": _instrument_admin(request.token),
            },
            "inputHoldings": [holding.contract_id for holding in selected_holdings],
            "executeBefore": execute_before.isoformat().replace("+00:00", "Z"),
        }

    def _holding_candidate_from_contract(self, contract: dict[str, Any]) -> HoldingCandidate | None:
        interface_views = contract.get("interface_views") or {}
        view = {}
        if isinstance(interface_views, dict):
            view = (
                interface_views.get(self.holding_interface_id)
                or interface_views.get(DEFAULT_HOLDING_INTERFACE_ID)
                or next(iter(interface_views.values()), {})
            )
        if not isinstance(view, dict):
            view = {}
        view = _unwrap_view_payload(view)
        payload = contract.get("payload") if isinstance(contract.get("payload"), dict) else {}
        payload = _unwrap_view_payload(payload)
        source = {**payload, **view}
        contract_id = str(contract.get("contract_id") or "")
        if not contract_id:
            return None
        owner = _find_owner(source)
        instrument = _find_instrument(source)
        if not instrument:
            instrument = _instrument_from_contract_template(contract)
        amount = _find_amount(source)
        if not owner or not instrument or amount is None:
            return None
        return HoldingCandidate(
            contract_id=contract_id,
            amount=amount,
            owner=owner,
            instrument=instrument,
            interface_view=view,
            created_event_blob=str(contract.get("created_event_blob") or ""),
            payload_keys=sorted(source.keys()),
            locked=_is_locked(source),
        )

    def _resolved_factory_endpoint(self) -> str:
        endpoint = self.transfer_factory_endpoint.strip()
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if self.utility_api_url:
            return urljoin(self.utility_api_url.rstrip("/") + "/", endpoint.lstrip("/"))
        return endpoint

    def _result(
        self,
        request: TokenTransferRequest,
        *,
        status: str,
        transfer_instruction_cid: str | None = None,
        holding_cid: str | None = None,
        executed_at: str = "",
        raw_provider_reference: str = "",
        error_message: str = "",
        proof_payload: dict[str, Any] | None = None,
    ) -> TokenTransferResult:
        return TokenTransferResult(
            status=status,
            token=request.token,
            sender=request.sender_party,
            receiver=request.receiver_party,
            amount=request.amount,
            transferReference=request.transfer_reference,
            transferInstructionCid=transfer_instruction_cid,
            holdingCid=holding_cid,
            executedAt=executed_at,
            provider_name=self.provider_name,
            raw_provider_reference=raw_provider_reference,
            error_message=error_message,
            proof_payload=proof_payload or {},
        )

    def p2plending_schema_summary(self) -> dict[str, Any]:
        return {
            "schema_confirmed": True,
            "schema_source": "Ledger API package archive symbol inspection",
            "package_id": P2PLENDING_USDCX_PACKAGE_ID,
            "module": P2PLENDING_USDCX_MODULE,
            "choices": P2PLENDING_SCHEMA,
            "partial_transfer_supported": False,
            "partial_transfer_reason": (
                "USDCx_Transfer/USDCx_TransferByOwner take holdingCid/newOwner and no amount field, "
                "so they transfer whole holdings."
            ),
        }

    def build_p2plending_transfer_plan(self, request: TokenTransferRequest) -> P2PLendingTransferPlan:
        settings = load_ledger_auth_settings()
        client = LedgerClient(settings)
        parties = _dedupe([request.sender_party, *default_read_parties()])
        holdings = self.list_usdcx_holdings(request)
        schema = self.p2plending_schema_summary()
        blockers: list[str] = []

        if _instrument_id(request.token) != "USDCx":
            blockers.append("P2PLending provider only supports configured USDCx instrumentId.")
        expected_admin = _instrument_admin(request.token)
        if expected_admin and expected_admin != DEFAULT_USDCX_INSTRUMENT_ADMIN:
            blockers.append("Configured USDCx instrumentAdmin does not match the confirmed DevNet/TestNet admin.")

        exact_amount_holding = _find_exact_holding(holdings, request.amount)
        selected_holding = exact_amount_holding
        if selected_holding is None:
            try:
                selected = self.select_input_holdings(holdings, request.amount)
            except InsufficientHoldingsError as exc:
                selected = []
                blockers.append(str(exc))
            if len(selected) == 1:
                selected_holding = selected[0]
            elif len(selected) > 1:
                blockers.append("P2PLending custom transfer currently requires one sufficient source holding.")

        requested_amount = _decimal(request.amount)
        split_required = bool(selected_holding and selected_holding.amount > requested_amount)
        if split_required and P2PLENDING_SPLIT_CHOICE not in P2PLENDING_SCHEMA:
            blockers.append("Split is required but USDCxHolding_Split schema is not confirmed.")
        if selected_holding and selected_holding.amount < requested_amount:
            blockers.append("Selected holding amount is below requested amount.")

        registry_contract = None
        if selected_holding is not None:
            registry_party = _registry_party_from_holding(selected_holding)
            if not registry_party:
                registry_party = _registry_party_for_holding(
                    client=client,
                    holding_cid=selected_holding.contract_id,
                    parties=parties,
                )
            registry_contract = self._find_p2plending_registry_contract(
                client=client,
                parties=parties,
                registry_party=registry_party,
            )
            if registry_contract is None:
                blockers.append("Active P2PLending USDCxRegistry contract could not be found for the visible holding registry party.")

        transfer_holding_cid = selected_holding.contract_id if selected_holding else ""
        transfer_argument = {
            "sender": request.sender_party,
            "holdingCid": transfer_holding_cid,
            "newOwner": request.receiver_party,
        }
        split_argument = {}
        if split_required:
            split_argument = {
                "actor": request.sender_party,
                "splitAmount": decimal_to_daml(request.amount),
            }

        if not schema.get("schema_confirmed"):
            blockers.append("P2PLending choice schema is not confirmed.")
        if not request.sender_party:
            blockers.append("Sender party is required for P2PLending transfer actAs.")

        return P2PLendingTransferPlan(
            ready=not blockers,
            selected_holding=selected_holding,
            exact_amount_holding=exact_amount_holding,
            registry_contract=registry_contract,
            split_required=split_required,
            act_as=request.sender_party,
            transfer_argument=transfer_argument,
            split_argument=split_argument,
            blockers=blockers,
            schema=schema,
        )

    def _execute_p2plending_transfer(self, request: TokenTransferRequest) -> TokenTransferResult:
        try:
            plan = self.build_p2plending_transfer_plan(request)
        except InsufficientHoldingsError as exc:
            return self._result(request, status=TRANSFER_FAILED, error_message=str(exc))
        except (LedgerSyncError, LedgerSubmissionError, requests.RequestException, ValueError) as exc:
            return self._result(request, status=TRANSFER_UNAVAILABLE, error_message=safe_error_message(exc))

        if not plan.ready:
            return self._result(
                request,
                status=TRANSFER_UNAVAILABLE,
                error_message="; ".join(plan.blockers) or "P2PLending USDCx transfer is not ready.",
            )

        settings = load_ledger_auth_settings()
        client = LedgerClient(settings)
        read_as = _dedupe(default_read_parties())
        transfer_holding = plan.selected_holding

        try:
            if plan.split_required:
                split_result = self._submit_p2plending_choice(
                    client=client,
                    request=request,
                    read_as=read_as,
                    template_id=_p2p_holding_template_id(),
                    contract_id=plan.selected_holding.contract_id,
                    choice=P2PLENDING_SPLIT_CHOICE,
                    argument=plan.split_argument,
                    workflow_suffix="split",
                )
                split_holding = self._holding_from_command_response(
                    split_result.raw_response,
                    owner=request.sender_party,
                    amount=request.amount,
                )
                if split_holding is None:
                    return self._result(
                        request,
                        status=TRANSFER_FAILED,
                        error_message="P2PLending split result did not expose a requested-amount sender holding.",
                        raw_provider_reference=split_result.update_id or "",
                    )
                transfer_holding = split_holding

            transfer_argument = {
                **plan.transfer_argument,
                "holdingCid": transfer_holding.contract_id,
            }
            transfer_result = self._submit_p2plending_choice(
                client=client,
                request=request,
                read_as=read_as,
                template_id=_p2p_registry_template_id(),
                contract_id=str(plan.registry_contract.get("contract_id") or ""),
                choice=P2PLENDING_TRANSFER_CHOICE,
                argument=transfer_argument,
                workflow_suffix="transfer",
            )
        except LedgerSubmissionError as exc:
            return self._result(request, status=TRANSFER_FAILED, error_message=safe_error_message(exc))

        receiver_holding = self._holding_from_command_response(
            transfer_result.raw_response,
            owner=request.receiver_party,
            amount=request.amount,
        )
        if receiver_holding is None:
            return self._result(
                request,
                status=TRANSFER_FAILED,
                error_message="P2PLending transfer result did not expose a verified receiver holding.",
                raw_provider_reference=transfer_result.update_id or "",
            )

        executed_at = (
            _find_first_string(
                transfer_result.raw_response,
                ("recordTime", "effectiveAt", "executedAt", "createdAt", "created_at"),
            )
            or timezone.now().isoformat().replace("+00:00", "Z")
        )
        proof = {
            "token": request.token,
            "sender": request.sender_party,
            "receiver": request.receiver_party,
            "amount": decimal_to_daml(request.amount),
            "transferReference": request.transfer_reference,
            "transferInstructionCid": None,
            "holdingCid": receiver_holding.contract_id,
            "executedAt": executed_at,
        }
        return self._result(
            request,
            status=TRANSFER_COMPLETED,
            holding_cid=receiver_holding.contract_id,
            executed_at=executed_at,
            raw_provider_reference=transfer_result.update_id or "",
            proof_payload=proof,
        )

    def _submit_p2plending_choice(
        self,
        *,
        client: LedgerClient,
        request: TokenTransferRequest,
        read_as: Sequence[str],
        template_id: str,
        contract_id: str,
        choice: str,
        argument: dict[str, Any],
        workflow_suffix: str,
    ) -> LedgerCommandResult:
        if not contract_id:
            raise LedgerSubmissionError("P2PLending contract id is required before command submission.")
        context = CommandContext(
            act_as=[request.sender_party],
            read_as=read_as,
            command_id=f"zalary-p2plending-usdcx-{workflow_suffix}-{uuid.uuid4().hex}",
            workflow_id=f"zalary-p2plending-usdcx-{workflow_suffix}-{request.transfer_reference}",
        )
        return client.submit_exercise_interface(
            context=context,
            interface_id=template_id,
            contract_id=contract_id,
            choice=choice,
            argument=argument,
        )

    def _holding_from_command_response(
        self,
        response: dict[str, Any] | list[Any],
        *,
        owner: str,
        amount: str,
    ) -> HoldingCandidate | None:
        required = _decimal(amount)
        for contract in normalize_active_contracts_response(response):
            candidate = self._holding_candidate_from_contract(contract)
            if candidate is None:
                continue
            if (
                candidate.owner == owner
                and candidate.amount == required
                and _instrument_matches(candidate.instrument, {"instrumentId": "USDCx"})
            ):
                return candidate
        return None

    def _find_p2plending_registry_contract(
        self,
        *,
        client: LedgerClient,
        parties: Sequence[str],
        registry_party: str = "",
    ) -> dict[str, Any] | None:
        try:
            contracts = client.query_active_contracts_by_template_identifier(
                package_id=P2PLENDING_USDCX_PACKAGE_ID,
                module_name=P2PLENDING_USDCX_MODULE,
                entity_name=P2PLENDING_USDCX_REGISTRY_TEMPLATE,
                parties=parties,
            )
        except LedgerSyncError:
            return None
        for contract in contracts:
            payload = contract.get("payload") if isinstance(contract.get("payload"), dict) else {}
            text = f"{payload} {contract.get('signatories') or []} {contract.get('observers') or []}"
            if not registry_party or registry_party in text:
                return contract
        return contracts[0] if contracts and not registry_party else None

    def safe_config(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "utility_api_url_configured": bool(self.utility_api_url),
            "xreserve_api_url_configured": bool(self.xreserve_api_url),
            "provider_mode": self.provider_mode,
            "timeout_seconds": self.timeout_seconds,
            "holding_interface_id": self.holding_interface_id,
            "transfer_factory_interface_id": self.transfer_factory_interface_id,
            "transfer_instruction_interface_id": self.transfer_instruction_interface_id,
            "transfer_factory_endpoint_configured": bool(self.transfer_factory_endpoint),
            "auto_accept_pending_transfer": self.auto_accept_pending_transfer,
            "allow_canonical_transfer_argument": self.allow_canonical_transfer_argument,
            "transfer_argument_shape": self.transfer_argument_shape,
        }


class InsufficientHoldingsError(ValueError):
    pass


def _dedupe(values: Sequence[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        item = (value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _p2p_holding_template_id() -> str:
    return f"{P2PLENDING_USDCX_PACKAGE_ID}:{P2PLENDING_USDCX_MODULE}:{P2PLENDING_USDCX_HOLDING_TEMPLATE}"


def _p2p_registry_template_id() -> str:
    return f"{P2PLENDING_USDCX_PACKAGE_ID}:{P2PLENDING_USDCX_MODULE}:{P2PLENDING_USDCX_REGISTRY_TEMPLATE}"


def _find_exact_holding(holdings: Sequence[HoldingCandidate], amount: str | Decimal) -> HoldingCandidate | None:
    required = _decimal(amount)
    matches = [holding for holding in holdings if holding.amount == required]
    if not matches:
        return None
    return sorted(matches, key=lambda item: item.contract_id)[0]


def _registry_party_from_holding(holding: HoldingCandidate) -> str:
    value = holding.interface_view.get("registry") if isinstance(holding.interface_view, dict) else ""
    if isinstance(value, dict):
        return str(value.get("party") or value.get("value") or value.get("text") or "")
    return str(value or "")


def _registry_party_for_holding(*, client: LedgerClient, holding_cid: str, parties: Sequence[str]) -> str:
    if not holding_cid:
        return ""
    try:
        contract = client.fetch_visible_created_event_by_contract_id(
            contract_id=holding_cid,
            parties=parties,
        )
    except LedgerSyncError:
        return ""
    if not contract:
        return ""
    payload = contract.get("payload") if isinstance(contract.get("payload"), dict) else {}
    value = payload.get("registry")
    if isinstance(value, dict):
        return str(value.get("party") or value.get("value") or value.get("text") or "")
    return str(value or "")


def _truncate_contract_id(contract_id: str) -> str:
    value = str(contract_id or "")
    if len(value) <= 24:
        return value
    return f"{value[:12]}...{value[-12:]}"


def _sanitize_choice_argument(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key.lower().endswith("cid") or key.lower() in {"contractid", "holdingcid", "othercid"}:
                sanitized[key] = _truncate_contract_id(str(item or ""))
            else:
                sanitized[key] = _sanitize_choice_argument(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_choice_argument(item) for item in value]
    return value


def _argument_shape_attempt_order(preferred_shape: str, *, allow_canonical: bool) -> list[str]:
    preferred = preferred_shape if preferred_shape in SUPPORTED_TRANSFER_ARGUMENT_SHAPES else TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS
    shapes = [preferred]
    if (
        allow_canonical
        and preferred == TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS
        and TRANSFER_ARGUMENT_SHAPE_CANONICAL_FLAT not in shapes
    ):
        shapes.append(TRANSFER_ARGUMENT_SHAPE_CANONICAL_FLAT)
    return shapes


def _allow_pending_offer(request: TokenTransferRequest) -> bool:
    metadata = request.metadata or {}
    value = metadata.get("allow_pending") or metadata.get("allow_offer") or metadata.get("allow_pending_offer")
    if isinstance(value, bool):
        return value
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Decimal value is invalid.") from exc


def _find_owner(source: dict[str, Any]) -> str:
    value = _find_first_value(source, ("owner", "accountOwner", "holder", "party"))
    if isinstance(value, dict):
        return str(value.get("party") or value.get("owner") or value.get("id") or "")
    return str(value or "")


def _find_instrument(source: dict[str, Any]) -> dict[str, Any]:
    instrument = _find_first_value(source, ("instrumentId", "instrument", "token", "tokenInstrument"))
    if isinstance(instrument, dict):
        return _canonical_instrument(instrument)
    if instrument:
        return {"instrumentId": str(instrument)}
    instrument_id = _find_first_string(source, ("id", "symbol"))
    instrument_admin = _find_first_string(source, ("admin", "instrumentAdmin"))
    if instrument_id or instrument_admin:
        return {"instrumentId": instrument_id or "", "instrumentAdmin": instrument_admin or ""}
    return {}


def _instrument_from_contract_template(contract: dict[str, Any]) -> dict[str, Any]:
    template_info = contract.get("template_id") if isinstance(contract.get("template_id"), dict) else {}
    module_name = str(template_info.get("module_name") or "")
    entity_name = str(template_info.get("entity_name") or "")
    for value in (module_name, entity_name):
        if "USDCx" in value:
            return {"symbol": "USDCx", "instrumentId": "USDCx", "instrumentAdmin": ""}
    return {}


def _canonical_instrument(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(value.get("symbol") or value.get("name") or value.get("instrumentId") or value.get("id") or ""),
        "instrumentId": _instrument_id(value),
        "instrumentAdmin": _instrument_admin(value),
        **{
            key: item
            for key, item in value.items()
            if key not in {"symbol", "name", "instrumentId", "id", "instrumentAdmin", "admin"}
        },
    }


def _instrument_id(value: dict[str, Any]) -> str:
    nested = value.get("instrumentId")
    if isinstance(nested, dict):
        return str(nested.get("id") or nested.get("instrumentId") or nested.get("text") or "")
    return str(value.get("instrumentId") or value.get("id") or value.get("symbol") or "")


def _instrument_admin(value: dict[str, Any]) -> str:
    nested = value.get("instrumentId")
    if isinstance(nested, dict):
        return str(nested.get("admin") or nested.get("instrumentAdmin") or "")
    return str(value.get("instrumentAdmin") or value.get("admin") or "")


def _instrument_matches(candidate: dict[str, Any], expected: dict[str, Any]) -> bool:
    candidate_id = _instrument_id(candidate)
    expected_id = _instrument_id(expected)
    candidate_admin = _instrument_admin(candidate)
    expected_admin = _instrument_admin(expected)
    if not (candidate_id and expected_id and candidate_id == expected_id):
        return False
    if expected_admin and candidate_admin and candidate_admin != expected_admin:
        return False
    return True


def _find_amount(source: dict[str, Any]) -> Decimal | None:
    value = _find_first_value(source, ("amount", "balance", "unlockedAmount", "quantity"))
    if isinstance(value, dict):
        value = _find_first_value(value, ("amount", "value", "numeric", "decimal"))
    if value is None:
        return None
    return _decimal(value)


def _is_locked(source: dict[str, Any]) -> bool:
    for key in ("locked", "lock", "lockContext", "encumbrance", "lockOwner", "locks"):
        value = source.get(key)
        if value in (None, False, "", [], {}):
            continue
        if isinstance(value, dict) and value.get("tag") in {"None", "Unlocked", "NoLock"}:
            continue
        return True
    return False


def _unwrap_view_payload(value: Any) -> dict[str, Any]:
    current = value
    while isinstance(current, dict) and set(current.keys()) <= {"tag", "value"} and "value" in current:
        next_value = current.get("value")
        if next_value is current:
            break
        current = next_value
    return current if isinstance(current, dict) else {}


def _safe_url_label(url: str) -> str:
    if not url:
        return ""
    without_query = url.split("?", 1)[0]
    return without_query[:180]


def _safe_response_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    lowered = cleaned.lower()
    if "authorization" in lowered or "bearer " in lowered or "client_secret" in lowered:
        return "[redacted]"
    if len(cleaned) > 240:
        return f"{cleaned[:240]}..."
    return cleaned


def _find_first_string(value: Any, keys: Sequence[str]) -> str | None:
    found = _find_first_value(value, keys)
    if found is None:
        return None
    if isinstance(found, (dict, list)):
        return None
    text = str(found)
    return text if text else None


def _find_first_dict(value: Any, keys: Sequence[str]) -> dict[str, Any] | None:
    found = _find_first_value(value, keys)
    return found if isinstance(found, dict) else None


def _find_first_list(value: Any, keys: Sequence[str]) -> list[Any] | None:
    found = _find_first_value(value, keys)
    return found if isinstance(found, list) else None


def _find_first_value(value: Any, keys: Sequence[str]) -> Any:
    if isinstance(value, list):
        for item in value:
            found = _find_first_value(item, keys)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None

    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    for item in value.values():
        found = _find_first_value(item, keys)
        if found is not None:
            return found
    return None


def _find_created_contract_id(value: Any, *, contains: str) -> str | None:
    needle = contains.lower()
    if isinstance(value, list):
        for item in value:
            found = _find_created_contract_id(item, contains=contains)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None

    event = None
    for key in ("createdEvent", "created_event", "createEvent", "create_event", "created"):
        item = value.get(key)
        if isinstance(item, dict):
            event = item
            break
    if event is None and ("contractId" in value or "contract_id" in value):
        event = value

    if isinstance(event, dict):
        template_text = str(
            event.get("templateId")
            or event.get("template_id")
            or event.get("template")
            or event.get("identifier")
            or ""
        ).lower()
        interface_text = str(
            event.get("interfaceId")
            or event.get("interface_id")
            or event.get("interfaceViews")
            or event.get("interface_views")
            or ""
        ).lower()
        if needle in template_text or needle in interface_text:
            contract_id = event.get("contractId") or event.get("contract_id")
            if contract_id:
                return str(contract_id)

    for item in value.values():
        found = _find_created_contract_id(item, contains=contains)
        if found:
            return found
    return None


def _find_first_variant_tag(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            found = _find_first_variant_tag(item)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None

    variant = value.get("variant")
    if isinstance(variant, dict):
        tag = variant.get("constructor") or variant.get("tag")
        if tag:
            return str(tag)
    for key in ("tag", "constructor"):
        if key in value and value[key]:
            return str(value[key])
    for item in value.values():
        found = _find_first_variant_tag(item)
        if found:
            return found
    return None
