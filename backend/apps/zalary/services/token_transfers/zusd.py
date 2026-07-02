from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
import uuid
from typing import Any, Sequence

from django.utils import timezone

from apps.zalary.models import ZUSDHoldingMirror
from apps.zalary.services.auth import default_read_parties, load_ledger_auth_settings
from apps.zalary.services.errors import ConfigurationError, LedgerSubmissionError, LedgerSyncError, safe_error_message
from apps.zalary.services.ledger import (
    CommandContext,
    LedgerClient,
    LedgerCommandResult,
    normalize_active_contracts_response,
    template_matches,
)
from apps.zalary.services.payloads import decimal_to_daml, token_transfer_proof_payload
from apps.zalary.services.templates import ZUSD_HOLDING

from .base import (
    BaseTokenTransferProvider,
    TokenTransferRequest,
    TokenTransferResult,
    TRANSFER_COMPLETED,
    TRANSFER_FAILED,
    TRANSFER_UNAVAILABLE,
)


ZALARY_TEST_TOKEN_PROVIDER_MODE = "zalary_test_token"
ZALARY_TEST_TOKEN_ENABLED = "ZALARY_TEST_TOKEN_ENABLED"
ZALARY_TEST_TOKEN_ENVIRONMENT = "ZALARY_TEST_TOKEN_ENVIRONMENT"
ZALARY_TEST_TOKEN_ISSUER_PARTY = "ZALARY_TEST_TOKEN_ISSUER_PARTY"
ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID = "ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID"
ZALARY_TEST_TOKEN_MAX_GRANT_AMOUNT = "ZALARY_TEST_TOKEN_MAX_GRANT_AMOUNT"
ZALARY_TEST_TOKEN_DAILY_LIMIT = "ZALARY_TEST_TOKEN_DAILY_LIMIT"
ZALARY_TEST_TOKEN_MONTHLY_LIMIT = "ZALARY_TEST_TOKEN_MONTHLY_LIMIT"

ZUSD_SYMBOL = "ZUSD"
ZUSD_INSUFFICIENT_HOLDINGS_ERROR = "Insufficient ZUSD holdings for sender wallet."
ZUSD_NOT_ENABLED_ERROR = (
    "ZUSD sandbox transfer provider is not explicitly enabled. "
    "Set ZALARY_TOKEN_TRANSFER_PROVIDER=zalary_test_token."
)


@dataclass(frozen=True)
class ZUSDHoldingCandidate:
    contract_id: str
    issuer: str
    owner: str
    amount: Decimal
    symbol: str
    reference: str
    observers: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "issuer": self.issuer,
            "owner": self.owner,
            "amount": decimal_to_daml(self.amount),
            "symbol": self.symbol,
            "reference": self.reference,
        }


@dataclass(frozen=True)
class ZUSDTransferPlan:
    ready: bool
    selected_holding: ZUSDHoldingCandidate | None = None
    receiver_party: str = ""
    amount: str = ""
    settlement_reference: str = ""
    blockers: list[str] = field(default_factory=list)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "selected_holding": (
                self.selected_holding.safe_summary()
                if self.selected_holding is not None
                else None
            ),
            "receiver_party": self.receiver_party,
            "amount": self.amount,
            "settlement_reference": self.settlement_reference,
            "can_submit": self.ready,
            "blockers": self.blockers,
        }


class ConfiguredZUSDTransferProvider(BaseTokenTransferProvider):
    provider_name = ZALARY_TEST_TOKEN_PROVIDER_MODE

    def __init__(self, *, require_explicit_provider: bool = True):
        self.require_explicit_provider = require_explicit_provider

    def execute_transfer(self, request: TokenTransferRequest) -> TokenTransferResult:
        plan = self.build_transfer_plan(request)
        if not plan.ready or plan.selected_holding is None:
            return TokenTransferResult(
                status=TRANSFER_FAILED if plan.blockers else TRANSFER_UNAVAILABLE,
                token=request.token,
                sender=request.sender_party,
                receiver=request.receiver_party,
                amount=request.amount,
                transferReference=request.transfer_reference,
                provider_name=self.provider_name,
                error_message="; ".join(plan.blockers) or ZUSD_NOT_ENABLED_ERROR,
            )

        try:
            command_result = self._submit_transfer_choice(request, plan.selected_holding)
        except (LedgerSubmissionError, LedgerSyncError, ConfigurationError) as exc:
            return _failed_result(request, safe_error_message(exc))

        receiver_holding = self._receiver_holding_from_response(
            command_result.raw_response,
            request=request,
            expected_issuer=plan.selected_holding.issuer,
        )
        if receiver_holding is None:
            return _failed_result(
                request,
                "ZUSD transfer did not produce a verified receiver holding with the expected owner, amount, symbol, and reference.",
            )

        executed_at = timezone.now().isoformat()
        transfer_instruction_cid = command_result.update_id or command_result.command_id
        proof_payload = token_transfer_proof_payload(
            token=request.token,
            sender=request.sender_party,
            receiver=request.receiver_party,
            amount=request.amount,
            transfer_reference=request.transfer_reference,
            transfer_instruction_cid=transfer_instruction_cid,
            holding_cid=receiver_holding.contract_id,
            executed_at=executed_at,
        )
        return TokenTransferResult(
            status=TRANSFER_COMPLETED,
            token=request.token,
            sender=request.sender_party,
            receiver=request.receiver_party,
            amount=decimal_to_daml(request.amount),
            transferReference=request.transfer_reference,
            transferInstructionCid=transfer_instruction_cid,
            holdingCid=receiver_holding.contract_id,
            executedAt=executed_at,
            provider_name=self.provider_name,
            raw_provider_reference=command_result.update_id or "",
            proof_payload=proof_payload,
        )

    def build_transfer_plan(self, request: TokenTransferRequest) -> ZUSDTransferPlan:
        blockers = self._preflight_request(request)
        if blockers:
            return ZUSDTransferPlan(
                ready=False,
                receiver_party=request.receiver_party,
                amount=str(request.amount),
                settlement_reference=request.transfer_reference,
                blockers=blockers,
            )

        try:
            holdings = self.list_zusd_holdings(owner_party=request.sender_party)
            selected = select_zusd_holding(holdings, request.amount)
        except (ConfigurationError, LedgerSyncError, ValueError) as exc:
            return ZUSDTransferPlan(
                ready=False,
                receiver_party=request.receiver_party,
                amount=decimal_to_daml(request.amount),
                settlement_reference=request.transfer_reference,
                blockers=[safe_error_message(exc)],
            )

        return ZUSDTransferPlan(
            ready=True,
            selected_holding=selected,
            receiver_party=request.receiver_party,
            amount=decimal_to_daml(request.amount),
            settlement_reference=request.transfer_reference,
        )

    def list_zusd_holdings(self, *, owner_party: str) -> list[ZUSDHoldingCandidate]:
        if self.require_explicit_provider and not provider_mode_enabled():
            raise ConfigurationError(ZUSD_NOT_ENABLED_ERROR)
        settings = load_ledger_auth_settings()
        client = LedgerClient(settings)
        parties = _query_parties(owner_party)
        contracts = client.query_active_contracts(template=ZUSD_HOLDING, parties=parties)
        candidates = []
        for contract in contracts:
            candidate = holding_candidate_from_contract(contract)
            if candidate is None:
                continue
            if candidate.owner != owner_party or candidate.symbol != ZUSD_SYMBOL or candidate.amount <= 0:
                continue
            upsert_zusd_holding(contract)
            candidates.append(candidate)
        return sorted(candidates, key=lambda holding: (holding.amount, holding.contract_id))

    def _submit_transfer_choice(
        self,
        request: TokenTransferRequest,
        selected_holding: ZUSDHoldingCandidate,
    ) -> LedgerCommandResult:
        settings = load_ledger_auth_settings()
        client = LedgerClient(settings)
        command_id = f"zalary-zusd-transfer-{uuid.uuid4().hex}"
        return client.submit_exercise(
            context=CommandContext(
                act_as=[request.sender_party],
                read_as=_query_parties(request.sender_party, request.receiver_party, selected_holding.issuer),
                command_id=command_id,
                workflow_id=f"zalary-zusd-transfer-{request.transfer_reference}",
            ),
            template=ZUSD_HOLDING,
            contract_id=selected_holding.contract_id,
            choice="TransferZUSD",
            argument={
                "receiver": request.receiver_party,
                "amount": decimal_to_daml(request.amount),
                "reference": request.transfer_reference,
            },
        )

    def _receiver_holding_from_response(
        self,
        raw_response: dict[str, Any],
        *,
        request: TokenTransferRequest,
        expected_issuer: str,
    ) -> ZUSDHoldingCandidate | None:
        contracts = [
            contract
            for contract in normalize_active_contracts_response(raw_response)
            if template_matches(contract, ZUSD_HOLDING)
        ]
        if contracts:
            return _matching_receiver_holding(
                contracts,
                request=request,
                expected_issuer=expected_issuer,
            )

        try:
            query_contracts = LedgerClient(load_ledger_auth_settings()).query_active_contracts(
                template=ZUSD_HOLDING,
                parties=_query_parties(request.sender_party, request.receiver_party, expected_issuer),
            )
        except (ConfigurationError, LedgerSyncError):
            return None
        return _matching_receiver_holding(
            query_contracts,
            request=request,
            expected_issuer=expected_issuer,
        )

    def _preflight_request(self, request: TokenTransferRequest) -> list[str]:
        blockers = []
        if self.require_explicit_provider and not provider_mode_enabled():
            blockers.append(ZUSD_NOT_ENABLED_ERROR)
        if not _request_token_is_zusd(request.token):
            blockers.append("ZUSD provider can only settle ZUSD salary claims.")
        if not str(request.sender_party or "").strip():
            blockers.append("ZUSD sender party is required.")
        if not str(request.receiver_party or "").strip():
            blockers.append("ZUSD receiver party is required.")
        if not str(request.transfer_reference or "").strip():
            blockers.append("ZUSD settlement reference is required.")
        try:
            if _decimal(request.amount) <= 0:
                blockers.append("ZUSD transfer amount must be positive.")
        except ValueError as exc:
            blockers.append(str(exc))
        return blockers


def provider_mode_enabled() -> bool:
    return os.environ.get("ZALARY_TOKEN_TRANSFER_PROVIDER", "").strip().lower() == ZALARY_TEST_TOKEN_PROVIDER_MODE


def select_zusd_holding(holdings: Sequence[ZUSDHoldingCandidate], amount: Decimal | str) -> ZUSDHoldingCandidate:
    requested = _decimal(amount)
    sufficient = [holding for holding in holdings if holding.amount >= requested]
    if not sufficient:
        raise ValueError(ZUSD_INSUFFICIENT_HOLDINGS_ERROR)
    return sorted(sufficient, key=lambda holding: (holding.amount, holding.contract_id))[0]


def holding_candidate_from_contract(contract: dict[str, Any]) -> ZUSDHoldingCandidate | None:
    payload = contract.get("payload") or {}
    contract_id = str(contract.get("contract_id") or "")
    if not contract_id:
        return None
    try:
        amount = _decimal(payload.get("amount"))
    except ValueError:
        return None
    symbol = str(payload.get("symbol") or "")
    return ZUSDHoldingCandidate(
        contract_id=contract_id,
        issuer=str(payload.get("issuer") or ""),
        owner=str(payload.get("owner") or ""),
        amount=amount,
        symbol=symbol,
        reference=str(payload.get("reference") or ""),
        observers=list(payload.get("observers") or []),
        payload=payload,
    )


def upsert_zusd_holding(contract: dict[str, Any]) -> ZUSDHoldingMirror | None:
    candidate = holding_candidate_from_contract(contract)
    if candidate is None:
        return None
    return ZUSDHoldingMirror.objects.update_or_create(
        contract_id=candidate.contract_id,
        defaults={
            "issuer_party": candidate.issuer,
            "owner_party": candidate.owner,
            "amount": candidate.amount,
            "symbol": candidate.symbol,
            "reference": candidate.reference,
            "payload": candidate.payload,
            "ledger_active": True,
            "ledger_offset": str(contract.get("ledger_offset") or ""),
            "last_seen_at": timezone.now(),
        },
    )[0]


def zusd_token_instrument(*, issuer_party: str) -> dict[str, str]:
    return {
        "symbol": ZUSD_SYMBOL,
        "instrumentId": ZUSD_SYMBOL,
        "instrumentAdmin": issuer_party,
        "utilityApiUrl": "zalary://sandbox/zusd",
        "xReserveApiUrl": "zalary://sandbox/zusd",
    }


def _matching_receiver_holding(
    contracts: Sequence[dict[str, Any]],
    *,
    request: TokenTransferRequest,
    expected_issuer: str,
) -> ZUSDHoldingCandidate | None:
    expected_amount = _decimal(request.amount)
    for contract in contracts:
        candidate = holding_candidate_from_contract(contract)
        if candidate is None:
            continue
        if candidate.issuer != expected_issuer:
            continue
        if candidate.owner != request.receiver_party:
            continue
        if candidate.symbol != ZUSD_SYMBOL:
            continue
        if candidate.amount != expected_amount:
            continue
        if candidate.reference != request.transfer_reference:
            continue
        upsert_zusd_holding(contract)
        return candidate
    return None


def _failed_result(request: TokenTransferRequest, error_message: str) -> TokenTransferResult:
    return TokenTransferResult(
        status=TRANSFER_FAILED,
        token=request.token,
        sender=request.sender_party,
        receiver=request.receiver_party,
        amount=str(request.amount),
        transferReference=request.transfer_reference,
        provider_name=ZALARY_TEST_TOKEN_PROVIDER_MODE,
        error_message=error_message,
    )


def _request_token_is_zusd(token: dict[str, Any]) -> bool:
    return (
        str(token.get("symbol") or "").strip() == ZUSD_SYMBOL
        and str(token.get("instrumentId") or "").strip() == ZUSD_SYMBOL
    )


def _query_parties(*parties: str) -> list[str]:
    issuer_party = os.environ.get(ZALARY_TEST_TOKEN_ISSUER_PARTY, "").strip()
    return _dedupe_parties([*default_read_parties(), issuer_party, *parties])


def _dedupe_parties(parties: Sequence[str]) -> list[str]:
    seen = set()
    deduped = []
    for party in parties:
        cleaned = str(party or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _decimal(value: Any) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("ZUSD amount is invalid.") from exc
    if decimal.as_tuple().exponent < -10:
        raise ValueError("ZUSD amount cannot have more than 10 decimal places.")
    return Decimal(decimal_to_daml(decimal))
