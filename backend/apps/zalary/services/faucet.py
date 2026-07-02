from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import os
import uuid
from typing import Any, Sequence

from django.db.models import Sum
from django.utils import timezone

from apps.zalary.models import CommandStatus, FaucetRequestStatus, LedgerCommand, ZUSDFaucetRequest
from apps.zalary.services.auth import COMMAND_ID_PREFIX, default_read_parties, env_flag_enabled, load_ledger_auth_settings
from apps.zalary.services.errors import ConfigurationError, LedgerSubmissionError, LedgerSyncError, safe_error_message
from apps.zalary.services.ledger import (
    CommandContext,
    LedgerClient,
    LedgerCommandResult,
    normalize_active_contracts_response,
    template_matches,
)
from apps.zalary.services.payloads import decimal_to_daml
from apps.zalary.services.templates import ZUSD_FAUCET_GRANT, ZUSD_HOLDING, ZUSD_ISSUER
from apps.zalary.services.token_transfers.zusd import (
    ZALARY_TEST_TOKEN_DAILY_LIMIT,
    ZALARY_TEST_TOKEN_ENABLED,
    ZALARY_TEST_TOKEN_ENVIRONMENT,
    ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID,
    ZALARY_TEST_TOKEN_ISSUER_PARTY,
    ZALARY_TEST_TOKEN_MAX_GRANT_AMOUNT,
    ZALARY_TEST_TOKEN_MONTHLY_LIMIT,
    ZUSD_SYMBOL,
    ConfiguredZUSDTransferProvider,
    holding_candidate_from_contract,
    upsert_zusd_holding,
)


DEFAULT_MAX_GRANT_AMOUNT = Decimal("5000.0000000000")
DEFAULT_DAILY_LIMIT = Decimal("10000.0000000000")
DEFAULT_MONTHLY_LIMIT = Decimal("50000.0000000000")


@dataclass(frozen=True)
class ZUSDFaucetSettings:
    enabled: bool
    environment: str
    issuer_party: str
    issuer_contract_id: str
    max_grant_amount: Decimal
    daily_limit: Decimal
    monthly_limit: Decimal

    def safe_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "environment": self.environment,
            "issuer_party_configured": bool(self.issuer_party),
            "issuer_contract_id_configured": bool(self.issuer_contract_id),
            "max_grant_amount": decimal_to_daml(self.max_grant_amount),
            "daily_limit": decimal_to_daml(self.daily_limit),
            "monthly_limit": decimal_to_daml(self.monthly_limit),
        }


@dataclass(frozen=True)
class ZUSDMintResult:
    status: str
    request_id: str
    owner_party: str
    amount: str
    reference: str
    environment: str
    holding_contract_id: str = ""
    grant_contract_id: str = ""
    command_id: str = ""
    update_id: str = ""
    ledger_command_id: int | None = None
    reason: str = ""

    def safe_summary(self) -> dict[str, Any]:
        summary = {
            "status": self.status,
            "request_id": self.request_id,
            "owner_party": self.owner_party,
            "amount": self.amount,
            "symbol": ZUSD_SYMBOL,
            "reference": self.reference,
            "environment": self.environment,
            "holding_contract_id": self.holding_contract_id,
            "grant_contract_id": self.grant_contract_id,
            "command_id": self.command_id,
            "update_id": self.update_id,
            "ledger_command_id": self.ledger_command_id,
        }
        if self.reason:
            summary["reason"] = self.reason
        return summary


@dataclass(frozen=True)
class ZUSDBalanceResult:
    owner_party: str
    symbol: str
    balance: str
    holding_count: int
    holdings: list[dict[str, Any]] = field(default_factory=list)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "owner_party": self.owner_party,
            "symbol": self.symbol,
            "balance": self.balance,
            "holding_count": self.holding_count,
            "holdings": self.holdings,
        }


def load_zusd_faucet_settings() -> ZUSDFaucetSettings:
    return ZUSDFaucetSettings(
        enabled=env_flag_enabled(ZALARY_TEST_TOKEN_ENABLED, default=False),
        environment=(os.environ.get(ZALARY_TEST_TOKEN_ENVIRONMENT, "sandbox").strip() or "sandbox"),
        issuer_party=os.environ.get(ZALARY_TEST_TOKEN_ISSUER_PARTY, "").strip(),
        issuer_contract_id=os.environ.get(ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID, "").strip(),
        max_grant_amount=_decimal_env(ZALARY_TEST_TOKEN_MAX_GRANT_AMOUNT, DEFAULT_MAX_GRANT_AMOUNT),
        daily_limit=_decimal_env(ZALARY_TEST_TOKEN_DAILY_LIMIT, DEFAULT_DAILY_LIMIT),
        monthly_limit=_decimal_env(ZALARY_TEST_TOKEN_MONTHLY_LIMIT, DEFAULT_MONTHLY_LIMIT),
    )


def get_zusd_balance(*, owner_party: str) -> ZUSDBalanceResult:
    settings = load_zusd_faucet_settings()
    if settings.environment != "sandbox":
        raise ConfigurationError("ZUSD sandbox token reads require ZALARY_TEST_TOKEN_ENVIRONMENT=sandbox.")
    owner = _required_party(owner_party, "owner_party")
    provider = ConfiguredZUSDTransferProvider(require_explicit_provider=False)
    holdings = provider.list_zusd_holdings(owner_party=owner)
    balance = sum((holding.amount for holding in holdings), Decimal("0"))
    return ZUSDBalanceResult(
        owner_party=owner,
        symbol=ZUSD_SYMBOL,
        balance=decimal_to_daml(balance),
        holding_count=len(holdings),
        holdings=[holding.safe_summary() for holding in holdings],
    )


def request_zusd_faucet_mint(
    *,
    owner_party: str,
    amount: Decimal | str,
    reference: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ZUSDMintResult:
    settings = load_zusd_faucet_settings()
    owner = _required_party(owner_party, "owner_party")
    requested_amount = _decimal(amount)
    resolved_request_id = _safe_text(request_id) or f"zusd-faucet-{uuid.uuid4().hex}"
    resolved_reference = _safe_text(reference) or resolved_request_id

    faucet_request = ZUSDFaucetRequest.objects.create(
        request_id=resolved_request_id,
        owner_party=owner,
        issuer_party=settings.issuer_party,
        amount=requested_amount,
        reference=resolved_reference,
        environment=settings.environment,
        status=FaucetRequestStatus.REQUESTED,
        metadata=metadata or {},
    )

    try:
        _validate_faucet_request(
            settings=settings,
            owner_party=owner,
            amount=requested_amount,
            request_id=faucet_request.request_id,
        )
        faucet_request.status = FaucetRequestStatus.APPROVED
        faucet_request.save(update_fields=["status", "updated_at"])
        result = _submit_mint(
            settings=settings,
            faucet_request=faucet_request,
            owner_party=owner,
            amount=requested_amount,
            reference=resolved_reference,
        )
    except Exception as exc:
        status = FaucetRequestStatus.REJECTED if isinstance(exc, ConfigurationError) else FaucetRequestStatus.FAILED
        error_message = safe_error_message(exc)
        faucet_request.status = status
        faucet_request.failure_reason = error_message
        faucet_request.save(update_fields=["status", "failure_reason", "updated_at"])
        raise

    return result


def zusd_faucet_history(*, owner_party: str | None = None) -> list[dict[str, Any]]:
    queryset = ZUSDFaucetRequest.objects.order_by("-created_at")
    if owner_party:
        queryset = queryset.filter(owner_party=owner_party)
    return [
        {
            "request_id": item.request_id,
            "owner_party": item.owner_party,
            "amount": decimal_to_daml(item.amount),
            "symbol": item.symbol,
            "reference": item.reference,
            "environment": item.environment,
            "status": item.status,
            "holding_contract_id": item.holding_contract_id,
            "grant_contract_id": item.grant_contract_id,
            "update_id": item.update_id,
            "failure_reason": item.failure_reason,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
            "minted_at": item.minted_at.isoformat() if item.minted_at else None,
        }
        for item in queryset[:100]
    ]


def _submit_mint(
    *,
    settings: ZUSDFaucetSettings,
    faucet_request: ZUSDFaucetRequest,
    owner_party: str,
    amount: Decimal,
    reference: str,
) -> ZUSDMintResult:
    issuer_contract_id = settings.issuer_contract_id or _find_issuer_contract_id(settings)
    if not issuer_contract_id:
        raise ConfigurationError("No active ZUSDIssuer contract is configured or visible.")

    payload = {
        "recipient": owner_party,
        "amount": decimal_to_daml(amount),
        "requestId": faucet_request.request_id,
        "reference": reference,
        "environment": settings.environment,
    }
    command_id = _new_command_id("zusd-mint")
    read_as = _dedupe_parties([*default_read_parties(), settings.issuer_party, owner_party])
    ledger_command = LedgerCommand.objects.create(
        command_id=command_id,
        workflow_id=f"zalary-zusd-mint-{faucet_request.request_id}",
        act_as=[settings.issuer_party],
        read_as=read_as,
        template_id=ZUSD_ISSUER.display_id(),
        contract_id=issuer_contract_id,
        choice_name="MintZUSD",
        payload=payload,
        status=CommandStatus.PENDING,
    )
    faucet_request.ledger_command = ledger_command
    faucet_request.save(update_fields=["ledger_command", "updated_at"])

    command_result = _submit_issuer_choice(
        settings=settings,
        issuer_contract_id=issuer_contract_id,
        payload=payload,
        command_id=command_id,
        read_as=read_as,
        ledger_command=ledger_command,
    )
    holding_cid, grant_cid = _mint_contract_ids_from_response(
        command_result.raw_response,
        owner_party=owner_party,
        amount=amount,
        request_id=faucet_request.request_id,
        reference=reference,
    )
    if not holding_cid or not grant_cid:
        raise LedgerSubmissionError("ZUSD mint did not return both a holding and faucet grant contract.")

    faucet_request.status = FaucetRequestStatus.MINTED
    faucet_request.holding_contract_id = holding_cid
    faucet_request.grant_contract_id = grant_cid
    faucet_request.update_id = command_result.update_id or ""
    faucet_request.minted_at = timezone.now()
    faucet_request.save(
        update_fields=[
            "status",
            "holding_contract_id",
            "grant_contract_id",
            "update_id",
            "minted_at",
            "updated_at",
        ]
    )
    return ZUSDMintResult(
        status="minted",
        request_id=faucet_request.request_id,
        owner_party=owner_party,
        amount=decimal_to_daml(amount),
        reference=reference,
        environment=settings.environment,
        holding_contract_id=holding_cid,
        grant_contract_id=grant_cid,
        command_id=command_id,
        update_id=command_result.update_id or "",
        ledger_command_id=ledger_command.pk,
    )


def _submit_issuer_choice(
    *,
    settings: ZUSDFaucetSettings,
    issuer_contract_id: str,
    payload: dict[str, Any],
    command_id: str,
    read_as: list[str],
    ledger_command: LedgerCommand,
) -> LedgerCommandResult:
    client = LedgerClient(load_ledger_auth_settings())
    try:
        ledger_command.status = CommandStatus.SUBMITTED
        ledger_command.submitted_at = timezone.now()
        ledger_command.save(update_fields=["status", "submitted_at", "updated_at"])
        result = client.submit_exercise(
            context=CommandContext(
                act_as=[settings.issuer_party],
                read_as=read_as,
                command_id=command_id,
                workflow_id=f"zalary-zusd-mint-{payload['requestId']}",
            ),
            template=ZUSD_ISSUER,
            contract_id=issuer_contract_id,
            choice="MintZUSD",
            argument=payload,
        )
    except Exception as exc:
        error_message = safe_error_message(exc)
        ledger_command.status = CommandStatus.FAILED
        ledger_command.error_message = error_message
        ledger_command.completed_at = timezone.now()
        ledger_command.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
        if isinstance(exc, LedgerSubmissionError):
            raise
        raise LedgerSubmissionError(error_message) from exc

    ledger_command.status = CommandStatus.SUCCEEDED
    ledger_command.update_id = result.update_id or ""
    ledger_command.completed_at = timezone.now()
    ledger_command.save(update_fields=["status", "update_id", "completed_at", "updated_at"])
    return result


def _find_issuer_contract_id(settings: ZUSDFaucetSettings) -> str:
    if not settings.issuer_party:
        raise ConfigurationError("ZALARY_TEST_TOKEN_ISSUER_PARTY is required for ZUSD faucet minting.")
    client = LedgerClient(load_ledger_auth_settings())
    contracts = client.query_active_contracts(
        template=ZUSD_ISSUER,
        parties=_dedupe_parties([*default_read_parties(), settings.issuer_party]),
    )
    for contract in contracts:
        payload = contract.get("payload") or {}
        if (
            payload.get("issuer") == settings.issuer_party
            and payload.get("symbol") == ZUSD_SYMBOL
            and payload.get("environment") == settings.environment
        ):
            return str(contract.get("contract_id") or "")
    return ""


def _mint_contract_ids_from_response(
    raw_response: dict[str, Any],
    *,
    owner_party: str,
    amount: Decimal,
    request_id: str,
    reference: str,
) -> tuple[str, str]:
    holding_cid = ""
    grant_cid = ""
    for contract in normalize_active_contracts_response(raw_response):
        payload = contract.get("payload") or {}
        if template_matches(contract, ZUSD_HOLDING):
            candidate = holding_candidate_from_contract(contract)
            if (
                candidate is not None
                and candidate.owner == owner_party
                and candidate.amount == amount
                and candidate.symbol == ZUSD_SYMBOL
                and candidate.reference == reference
            ):
                upsert_zusd_holding(contract)
                holding_cid = candidate.contract_id
        if template_matches(contract, ZUSD_FAUCET_GRANT):
            if (
                payload.get("recipient") == owner_party
                and _decimal(payload.get("amount")) == amount
                and payload.get("requestId") == request_id
                and payload.get("reference") == reference
                and payload.get("symbol") == ZUSD_SYMBOL
            ):
                grant_cid = str(contract.get("contract_id") or "")
    return holding_cid, grant_cid


def _validate_faucet_request(
    *,
    settings: ZUSDFaucetSettings,
    owner_party: str,
    amount: Decimal,
    request_id: str | None = None,
) -> None:
    if not settings.enabled:
        raise ConfigurationError("ZUSD faucet is disabled.")
    if settings.environment != "sandbox":
        raise ConfigurationError("ZUSD faucet only mints in sandbox environment.")
    if not settings.issuer_party:
        raise ConfigurationError("ZALARY_TEST_TOKEN_ISSUER_PARTY is required for ZUSD faucet minting.")
    if amount <= 0:
        raise ConfigurationError("ZUSD faucet amount must be positive.")
    if amount > settings.max_grant_amount:
        raise ConfigurationError("ZUSD faucet request exceeds max grant amount.")

    today_total = _request_total_since(
        owner_party=owner_party,
        environment=settings.environment,
        since=_start_of_day(),
        exclude_request_id=request_id,
    )
    if today_total + amount > settings.daily_limit:
        raise ConfigurationError("ZUSD faucet request exceeds daily limit.")

    month_total = _request_total_since(
        owner_party=owner_party,
        environment=settings.environment,
        since=_start_of_month(),
        exclude_request_id=request_id,
    )
    if month_total + amount > settings.monthly_limit:
        raise ConfigurationError("ZUSD faucet request exceeds monthly limit.")


def _request_total_since(
    *,
    owner_party: str,
    environment: str,
    since,
    exclude_request_id: str | None = None,
) -> Decimal:
    queryset = ZUSDFaucetRequest.objects.filter(
        owner_party=owner_party,
        environment=environment,
        status__in=[
            FaucetRequestStatus.REQUESTED,
            FaucetRequestStatus.APPROVED,
            FaucetRequestStatus.MINTED,
        ],
        created_at__gte=since,
    )
    if exclude_request_id:
        queryset = queryset.exclude(request_id=exclude_request_id)
    total = queryset.aggregate(total=Sum("amount"))["total"]
    return total or Decimal("0")


def _start_of_day():
    now = timezone.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_month():
    now = timezone.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _new_command_id(action: str) -> str:
    prefix = os.environ.get(COMMAND_ID_PREFIX, "").strip() or "zalary"
    return f"{prefix}-{action}-{uuid.uuid4().hex}"


def _required_party(value: str | None, field_name: str) -> str:
    text = _safe_text(value)
    if not text:
        raise ConfigurationError(f"{field_name} is required.")
    return text


def _safe_text(value: str | None) -> str:
    return str(value or "").strip()


def _decimal_env(name: str, default: Decimal) -> Decimal:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    return _decimal(value)


def _decimal(value: Any) -> Decimal:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigurationError("ZUSD faucet amount is invalid.") from exc
    if decimal.as_tuple().exponent < -10:
        raise ConfigurationError("ZUSD faucet amount cannot have more than 10 decimal places.")
    return Decimal(decimal_to_daml(decimal))


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
