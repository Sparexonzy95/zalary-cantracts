from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils import timezone

from apps.zalary.models import (
    ClaimTicketMirror,
    PayrollVaultMirror,
    SalaryAllocationMirror,
    SalaryClaimMirror,
    SettledSalaryRecordMirror,
    SettlementReceiptMirror,
    USDCxTransferRecord,
)

from .errors import (
    DuplicateSalaryClaimError,
    DuplicateSettlementError,
    OnboardingValidationError,
    SettlementProofError,
    safe_error_message,
)
from .idempotency import (
    find_existing_command,
    key_confirm_settlement,
    key_request_salary_claim,
)
from .payloads import (
    confirm_salary_settlement_choice_payload,
    decimal_to_daml,
    request_salary_claim_choice_payload,
)
from .payroll import (
    ACTIVE_STATUS,
    ALLOCATION_TICKET_ISSUED_STATUS,
    ARCHIVED_STATUS,
    PENDING_CLAIM_WINDOW_OPEN_STATUS,
    _same_token,
    _submit_choice,
    create_demo_funding_activation_ticket_pipeline,
)
from .sync import (
    AuditContractSyncResult,
    PayrollVaultSyncResult,
    SalaryAllocationSyncResult,
    SalaryClaimSyncResult,
    sync_claim_tickets,
    sync_failed_salary_claims,
    sync_payroll_vaults,
    sync_payslips,
    sync_salary_allocations,
    sync_salary_claims,
    sync_settled_salary_records,
    sync_settlement_receipts,
)
from .templates import CHOICES, CLAIM_TICKET, SALARY_CLAIM
from .token_transfers import TokenTransferRequest, TokenTransferResult, get_token_transfer_provider
from .token_transfers.base import TRANSFER_COMPLETED, TRANSFER_PENDING, TRANSFER_PENDING_RECEIVER_ACCEPTANCE
from .token_transfers.factory import demo_settlement_proof_enabled, external_proof_enabled


CLAIM_REQUESTED_STATUS = "ClaimRequested"
CLOSED_STATUS = "Closed"


@dataclass(frozen=True)
class SalaryClaimPreflightResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    claim_ticket_contract_id: str
    employee_wallet: str
    act_as: list[str]
    future_command: dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "claim_ticket_contract_id": self.claim_ticket_contract_id,
            "employee_wallet": self.employee_wallet,
            "act_as": self.act_as,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class SettlementPreflightResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    salary_claim_contract_id: str
    payroll_vault_contract_id: str
    employer_wallet: str
    settlement_reference: str
    claim_amount: str
    act_as: list[str]
    future_command: dict[str, Any]
    settlement_proof: dict[str, Any] = field(repr=False)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "salary_claim_contract_id": self.salary_claim_contract_id,
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "employer_wallet": self.employer_wallet,
            "settlement_reference": self.settlement_reference,
            "claim_amount": self.claim_amount,
            "act_as": self.act_as,
            "settlement_proof_validated": True,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class SalaryExecutionResult:
    status: str
    action: str
    command_id: str
    update_id: str | None
    company_id: str
    payroll_id: str
    employee_external_id: str
    ledger_command_pk: int
    contract_id: str = ""
    choice_name: str = ""
    salary_claim_contract_id: str = ""
    payroll_vault_contract_id: str = ""
    settlement_reference: str = ""
    transfer: dict[str, Any] = field(default_factory=dict)
    transfer_record_id: int | None = None
    synced_salary_claims: SalaryClaimSyncResult | None = None
    synced_payroll_vaults: PayrollVaultSyncResult | None = None
    synced_salary_allocations: SalaryAllocationSyncResult | None = None
    synced_settlement_receipts: AuditContractSyncResult | None = None
    synced_payslips: AuditContractSyncResult | None = None
    synced_settled_salary_records: AuditContractSyncResult | None = None
    synced_failed_salary_claims: AuditContractSyncResult | None = None
    existing_contract: dict[str, Any] | None = None
    reason: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "status": self.status,
            "action": self.action,
            "command_id": self.command_id,
            "update_id": self.update_id,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "ledger_command_id": self.ledger_command_pk,
            "contract_id": self.contract_id,
            "choice_name": self.choice_name,
        }
        if self.salary_claim_contract_id:
            summary["salary_claim_contract_id"] = self.salary_claim_contract_id
        if self.payroll_vault_contract_id:
            summary["payroll_vault_contract_id"] = self.payroll_vault_contract_id
        if self.settlement_reference:
            summary["settlement_reference"] = self.settlement_reference
        if self.transfer:
            summary["transfer"] = self.transfer
        if self.transfer_record_id is not None:
            summary["transfer_record_id"] = self.transfer_record_id
        if self.reason:
            summary["reason"] = self.reason
        if self.existing_contract is not None:
            summary["existing_contract"] = self.existing_contract
        if self.synced_salary_claims is not None:
            summary["synced_salary_claims"] = self.synced_salary_claims.safe_summary()
        if self.synced_payroll_vaults is not None:
            summary["synced_payroll_vaults"] = self.synced_payroll_vaults.safe_summary()
        if self.synced_salary_allocations is not None:
            summary["synced_salary_allocations"] = self.synced_salary_allocations.safe_summary()
        if self.synced_settlement_receipts is not None:
            summary["synced_settlement_receipts"] = self.synced_settlement_receipts.safe_summary()
        if self.synced_payslips is not None:
            summary["synced_payslips"] = self.synced_payslips.safe_summary()
        if self.synced_settled_salary_records is not None:
            summary["synced_settled_salary_records"] = self.synced_settled_salary_records.safe_summary()
        if self.synced_failed_salary_claims is not None:
            summary["synced_failed_salary_claims"] = self.synced_failed_salary_claims.safe_summary()
        return summary


@dataclass(frozen=True)
class DemoFullPayrollExecutionResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    setup_step: dict[str, Any]
    salary_claim_step: SalaryExecutionResult
    settlement_step: SalaryExecutionResult
    final_sync: dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "setup_step": self.setup_step,
            "salary_claim_step": self.salary_claim_step.safe_summary(),
            "settlement_step": self.settlement_step.safe_summary(),
            "final_sync": self.final_sync,
        }


@dataclass(frozen=True)
class SettlementProofResolution:
    proof: dict[str, Any]
    transfer_record: USDCxTransferRecord | None
    transfer_summary: dict[str, Any]
    status: str = TRANSFER_COMPLETED
    reason: str = ""


REQUIRED_TRANSFER_PROOF_FIELDS = {
    "token",
    "sender",
    "receiver",
    "amount",
    "transferReference",
    "executedAt",
}


def validate_transfer_proof_shape(proof: dict[str, Any]) -> None:
    if not isinstance(proof, dict):
        raise SettlementProofError("Settlement proof is required.")
    missing = REQUIRED_TRANSFER_PROOF_FIELDS - set(proof)
    if missing:
        names = ", ".join(sorted(missing))
        raise SettlementProofError(f"Settlement proof is missing required field(s): {names}.")


def validate_settlement_proof(
    *,
    claim: SalaryClaimMirror,
    settlement_reference: str,
    settlement_proof: dict[str, Any] | None,
) -> dict[str, Any]:
    reference = (settlement_reference or "").strip()
    if not reference:
        raise SettlementProofError("settlementReference is required.")
    if settlement_proof is None:
        raise SettlementProofError("settlementProof is required.")
    validate_transfer_proof_shape(settlement_proof)

    proof_token = settlement_proof.get("token") or {}
    claim_token = (claim.payload or {}).get("claimToken") or {}
    if not _same_token(proof_token, claim_token):
        raise SettlementProofError("Settlement proof token does not match the salary claim token.")
    if str(settlement_proof.get("sender") or "") != claim.employer_wallet_party:
        raise SettlementProofError("Settlement proof sender must match the employer wallet.")
    if str(settlement_proof.get("receiver") or "") != claim.employee_wallet_party:
        raise SettlementProofError("Settlement proof receiver must match the employee wallet.")
    if _decimal(settlement_proof.get("amount")) != _decimal(claim.claim_amount):
        raise SettlementProofError("Settlement proof amount must match the salary claim amount.")
    if str(settlement_proof.get("transferReference") or "").strip() != reference:
        raise SettlementProofError("Settlement proof transferReference must match settlementReference.")
    if not str(settlement_proof.get("executedAt") or "").strip():
        raise SettlementProofError("Settlement proof executedAt is required.")

    return settlement_proof


def preflight_request_salary_claim(
    *,
    claim_ticket_contract_id: str | None = None,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
) -> SalaryClaimPreflightResult:
    ticket = _claim_ticket_or_error(
        claim_ticket_contract_id=claim_ticket_contract_id,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if _active_salary_claim_for_employee(
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
    ) is not None:
        raise DuplicateSalaryClaimError(
            f"Salary claim already exists locally for {ticket.company_id}/{ticket.payroll_id}/{ticket.employee_external_id}."
        )
    if _settlement_for_employee(
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
    ) is not None:
        raise DuplicateSalaryClaimError("Salary has already been settled for this employee payroll entry.")

    return SalaryClaimPreflightResult(
        status="ok",
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
        claim_ticket_contract_id=ticket.contract_id,
        employee_wallet=ticket.employee_wallet_party,
        act_as=[ticket.employee_wallet_party],
        future_command={
            "template": CLAIM_TICKET.display_id(),
            "choice": CHOICES["ClaimTicket"]["RequestSalaryClaim"],
            "contract_id": ticket.contract_id,
        },
    )


def request_salary_claim(
    *,
    claim_ticket_contract_id: str | None = None,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> SalaryExecutionResult:
    ticket = _claim_ticket_or_error(
        claim_ticket_contract_id=claim_ticket_contract_id,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    existing_claim = _active_salary_claim_for_employee(
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
    )
    if existing_claim is not None:
        if allow_existing:
            return _existing_salary_claim_result(existing_claim, reason="Salary claim already exists.")
        raise DuplicateSalaryClaimError(
            f"Salary claim already exists locally for {ticket.company_id}/{ticket.payroll_id}/{ticket.employee_external_id}."
        )
    existing_settlement = _settlement_for_employee(
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
    )
    if existing_settlement is not None:
        if allow_existing:
            return _existing_salary_claim_result(
                None,
                ticket=ticket,
                existing_contract=_settlement_summary(existing_settlement),
                reason="Salary has already been settled.",
            )
        raise DuplicateSalaryClaimError("Salary has already been settled for this employee payroll entry.")

    workflow_id = key_request_salary_claim(
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
    )
    idempotent = find_existing_command(
        workflow_id=workflow_id,
        template_id=CLAIM_TICKET.display_id(),
        choice_name=CHOICES["ClaimTicket"]["RequestSalaryClaim"],
    )
    if idempotent is not None:
        return _idempotent_salary_claim_result(ticket=ticket, idempotent=idempotent)

    preflight = preflight_request_salary_claim(
        claim_ticket_contract_id=ticket.contract_id,
    )
    command_result = _submit_choice(
        action_slug="request-salary-claim",
        workflow_id=workflow_id,
        act_as=preflight.act_as,
        template=CLAIM_TICKET,
        contract_id=preflight.claim_ticket_contract_id,
        choice=CHOICES["ClaimTicket"]["RequestSalaryClaim"],
        payload=request_salary_claim_choice_payload(),
    )

    synced_salary_claims = None
    claim = None
    if sync_after:
        synced_salary_claims = sync_salary_claims(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        claim = _active_salary_claim_for_employee(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )

    return SalaryExecutionResult(
        status="ok",
        action="RequestSalaryClaim",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        employee_external_id=preflight.employee_external_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=claim.contract_id if claim is not None else "",
        salary_claim_contract_id=claim.contract_id if claim is not None else "",
        choice_name=CHOICES["ClaimTicket"]["RequestSalaryClaim"],
        synced_salary_claims=synced_salary_claims,
        raw_response=command_result["result"].raw_response,
    )


def preflight_confirm_salary_settlement(
    *,
    salary_claim_contract_id: str | None = None,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    settlement_reference: str,
    settlement_proof: dict[str, Any] | None = None,
    demo_proof: bool = False,
) -> SettlementPreflightResult:
    claim = _salary_claim_or_error(
        salary_claim_contract_id=salary_claim_contract_id,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    existing_settlement = _settlement_for_employee(
        company_id=claim.company_id,
        payroll_id=claim.payroll_id,
        employee_external_id=claim.employee_external_id,
    )
    if existing_settlement is not None:
        raise DuplicateSettlementError("Salary settlement already exists locally for this employee payroll entry.")
    if claim.claim_status != CLAIM_REQUESTED_STATUS:
        raise OnboardingValidationError("SalaryClaim must be ClaimRequested before settlement confirmation.")

    vault = _active_payroll_vault_or_error(company_id=claim.company_id, payroll_id=claim.payroll_id)
    if vault.vault_status not in {ACTIVE_STATUS, CLOSED_STATUS}:
        raise OnboardingValidationError("Payroll vault must be Active or Closed before settlement confirmation.")
    _validate_claim_matches_vault(claim, vault)
    _validate_claim_source_allocation(claim)

    proof = settlement_proof
    if proof is None and demo_proof:
        if not demo_settlement_proof_enabled():
            raise SettlementProofError(
                "demoProof is disabled. Set ZALARY_ENABLE_DEMO_SETTLEMENT_PROOF=true only for local/dev testing."
            )
        proof = demo_settlement_proof(claim=claim, settlement_reference=settlement_reference)
    validated_proof = validate_settlement_proof(
        claim=claim,
        settlement_reference=settlement_reference,
        settlement_proof=proof,
    )
    payload = _settlement_payload_or_error(
        payroll_vault_cid=vault.contract_id,
        settlement_reference=settlement_reference,
        settlement_proof=validated_proof,
    )

    return SettlementPreflightResult(
        status="ok",
        company_id=claim.company_id,
        payroll_id=claim.payroll_id,
        employee_external_id=claim.employee_external_id,
        salary_claim_contract_id=claim.contract_id,
        payroll_vault_contract_id=vault.contract_id,
        employer_wallet=claim.employer_wallet_party,
        settlement_reference=payload["settlementReference"],
        claim_amount=str(claim.claim_amount),
        act_as=[claim.employer_wallet_party],
        settlement_proof=payload["settlementProof"],
        future_command={
            "template": SALARY_CLAIM.display_id(),
            "choice": CHOICES["SalaryClaim"]["ConfirmSalarySettlement"],
            "contract_id": claim.contract_id,
        },
    )


def confirm_salary_settlement(
    *,
    salary_claim_contract_id: str | None = None,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    settlement_reference: str,
    settlement_proof: dict[str, Any] | None = None,
    demo_proof: bool = False,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> SalaryExecutionResult:
    claim = _salary_claim_or_error(
        salary_claim_contract_id=salary_claim_contract_id,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    existing_settlement = _settlement_for_employee(
        company_id=claim.company_id,
        payroll_id=claim.payroll_id,
        employee_external_id=claim.employee_external_id,
    )
    if existing_settlement is not None:
        if allow_existing:
            return _existing_settlement_result(claim=claim, settlement=existing_settlement)
        raise DuplicateSettlementError("Salary settlement already exists locally for this employee payroll entry.")

    resolved_reference = (settlement_reference or f"SETTLE-{claim.payroll_id}-{claim.employee_external_id}").strip()
    workflow_id = key_confirm_settlement(
        salary_claim_contract_id=claim.contract_id,
        settlement_reference=resolved_reference,
    )
    idempotent = find_existing_command(
        workflow_id=workflow_id,
        template_id=SALARY_CLAIM.display_id(),
        choice_name=CHOICES["SalaryClaim"]["ConfirmSalarySettlement"],
    )
    if idempotent is not None:
        return _idempotent_settlement_result(claim=claim, settlement_reference=resolved_reference, idempotent=idempotent)

    proof_resolution = _resolve_settlement_proof(
        claim=claim,
        settlement_reference=resolved_reference,
        settlement_proof=settlement_proof,
        demo_proof=demo_proof,
    )
    if proof_resolution.status in {TRANSFER_PENDING, TRANSFER_PENDING_RECEIVER_ACCEPTANCE}:
        return SalaryExecutionResult(
            status="pending",
            action="ConfirmSalarySettlement",
            command_id="",
            update_id=None,
            company_id=claim.company_id,
            payroll_id=claim.payroll_id,
            employee_external_id=claim.employee_external_id,
            ledger_command_pk=0,
            salary_claim_contract_id=claim.contract_id,
            settlement_reference=resolved_reference,
            transfer=proof_resolution.transfer_summary,
            transfer_record_id=(
                proof_resolution.transfer_record.pk if proof_resolution.transfer_record is not None else None
            ),
            choice_name=CHOICES["SalaryClaim"]["ConfirmSalarySettlement"],
            reason=proof_resolution.reason or "Token transfer is pending; Zalary settlement was not submitted.",
        )
    preflight = preflight_confirm_salary_settlement(
        salary_claim_contract_id=claim.contract_id,
        settlement_reference=resolved_reference,
        settlement_proof=proof_resolution.proof,
        demo_proof=False,
    )
    command_result = _submit_choice(
        action_slug="confirm-salary-settlement",
        workflow_id=workflow_id,
        act_as=preflight.act_as,
        template=SALARY_CLAIM,
        contract_id=preflight.salary_claim_contract_id,
        choice=CHOICES["SalaryClaim"]["ConfirmSalarySettlement"],
        payload={
            "payrollVaultCid": preflight.payroll_vault_contract_id,
            "settlementReference": preflight.settlement_reference,
            "settlementProof": preflight.settlement_proof,
        },
    )
    if proof_resolution.transfer_record is not None:
        proof_resolution.transfer_record.ledger_command = command_result["command"]
        proof_resolution.transfer_record.save(update_fields=["ledger_command", "updated_at"])

    synced_payroll_vaults = None
    synced_salary_allocations = None
    synced_salary_claims = None
    synced_settlement_receipts = None
    synced_payslips = None
    synced_settled_salary_records = None
    settlement = None
    if sync_after:
        synced_settlement_receipts = sync_settlement_receipts(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        synced_payslips = sync_payslips(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        synced_settled_salary_records = sync_settled_salary_records(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        synced_payroll_vaults = sync_payroll_vaults(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
        )
        synced_salary_allocations = sync_salary_allocations(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
        )
        synced_salary_claims = sync_salary_claims(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        settlement = _settlement_for_employee(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )

    return SalaryExecutionResult(
        status="ok",
        action="ConfirmSalarySettlement",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        employee_external_id=preflight.employee_external_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=settlement.contract_id if settlement is not None else "",
        salary_claim_contract_id=preflight.salary_claim_contract_id,
        payroll_vault_contract_id=preflight.payroll_vault_contract_id,
        settlement_reference=preflight.settlement_reference,
        transfer=proof_resolution.transfer_summary,
        transfer_record_id=proof_resolution.transfer_record.pk if proof_resolution.transfer_record is not None else None,
        choice_name=CHOICES["SalaryClaim"]["ConfirmSalarySettlement"],
        synced_salary_claims=synced_salary_claims,
        synced_payroll_vaults=synced_payroll_vaults,
        synced_salary_allocations=synced_salary_allocations,
        synced_settlement_receipts=synced_settlement_receipts,
        synced_payslips=synced_payslips,
        synced_settled_salary_records=synced_settled_salary_records,
        raw_response=command_result["result"].raw_response,
    )


def create_demo_full_payroll_execution(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str = "EMP-001",
    funding_amount: Decimal | str | None = None,
    funding_reference: str | None = None,
    settlement_reference: str | None = None,
    allow_existing: bool = False,
    demo_proof: bool = False,
) -> DemoFullPayrollExecutionResult:
    setup = create_demo_funding_activation_ticket_pipeline(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        funding_amount=funding_amount,
        funding_reference=funding_reference,
        allow_existing=True,
    )
    if setup.status == PENDING_CLAIM_WINDOW_OPEN_STATUS:
        raise OnboardingValidationError("Claim window has not opened; full payroll execution cannot continue.")

    claim_step = request_salary_claim(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        sync_after=True,
        allow_existing=allow_existing,
    )
    claim = _active_salary_claim_for_employee(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    ) or _latest_salary_claim_for_employee(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if claim is None:
        raise OnboardingValidationError("Salary claim was not found locally after request step.")

    resolved_reference = (settlement_reference or f"SETTLE-{payroll_id}-{employee_external_id}").strip()
    settlement_step = confirm_salary_settlement(
        salary_claim_contract_id=claim.contract_id,
        settlement_reference=resolved_reference,
        settlement_proof=None,
        demo_proof=demo_proof,
        sync_after=True,
        allow_existing=allow_existing,
    )
    final_sync = sync_final_payroll_execution_state(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )

    return DemoFullPayrollExecutionResult(
        status="ok",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        setup_step=setup.safe_summary(),
        salary_claim_step=claim_step,
        settlement_step=settlement_step,
        final_sync=final_sync,
    )


def run_full_payroll_execution(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str = "EMP-001",
    funding_amount: Decimal | str | None = None,
    funding_reference: str | None = None,
    settlement_reference: str | None = None,
    settlement_proof: dict[str, Any] | None = None,
    allow_existing: bool = False,
) -> DemoFullPayrollExecutionResult:
    setup = create_demo_funding_activation_ticket_pipeline(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        funding_amount=funding_amount,
        funding_reference=funding_reference,
        allow_existing=True,
    )
    if setup.status == PENDING_CLAIM_WINDOW_OPEN_STATUS:
        raise OnboardingValidationError("Claim window has not opened; full payroll execution cannot continue.")

    claim_step = request_salary_claim(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        sync_after=True,
        allow_existing=allow_existing,
    )
    claim = _active_salary_claim_for_employee(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    ) or _latest_salary_claim_for_employee(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if claim is None:
        raise OnboardingValidationError("Salary claim was not found locally after request step.")

    resolved_reference = (settlement_reference or f"SETTLE-{payroll_id}-{employee_external_id}").strip()
    settlement_step = confirm_salary_settlement(
        salary_claim_contract_id=claim.contract_id,
        settlement_reference=resolved_reference,
        settlement_proof=settlement_proof,
        demo_proof=False,
        sync_after=True,
        allow_existing=allow_existing,
    )
    final_sync = sync_final_payroll_execution_state(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    return DemoFullPayrollExecutionResult(
        status="ok",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        setup_step=setup.safe_summary(),
        salary_claim_step=claim_step,
        settlement_step=settlement_step,
        final_sync=final_sync,
    )


def sync_final_payroll_execution_state(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> dict[str, Any]:
    return {
        "salary_claims": sync_salary_claims(
            company_id=company_id,
            payroll_id=payroll_id,
            employee_external_id=employee_external_id,
        ).safe_summary(),
        "settlement_receipts": sync_settlement_receipts(
            company_id=company_id,
            payroll_id=payroll_id,
            employee_external_id=employee_external_id,
        ).safe_summary(),
        "payslips": sync_payslips(
            company_id=company_id,
            payroll_id=payroll_id,
            employee_external_id=employee_external_id,
        ).safe_summary(),
        "settled_salary_records": sync_settled_salary_records(
            company_id=company_id,
            payroll_id=payroll_id,
            employee_external_id=employee_external_id,
        ).safe_summary(),
        "failed_salary_claims": sync_failed_salary_claims(
            company_id=company_id,
            payroll_id=payroll_id,
            employee_external_id=employee_external_id,
        ).safe_summary(),
    }


def _resolve_settlement_proof(
    *,
    claim: SalaryClaimMirror,
    settlement_reference: str,
    settlement_proof: dict[str, Any] | None,
    demo_proof: bool,
) -> SettlementProofResolution:
    if demo_proof:
        if not demo_settlement_proof_enabled():
            raise SettlementProofError(
                "demoProof is disabled. Set ZALARY_ENABLE_DEMO_SETTLEMENT_PROOF=true only for local/dev testing."
            )
        proof = validate_settlement_proof(
            claim=claim,
            settlement_reference=settlement_reference,
            settlement_proof=demo_settlement_proof(claim=claim, settlement_reference=settlement_reference),
        )
        record = _create_transfer_record_from_proof(
            claim=claim,
            settlement_reference=settlement_reference,
            provider_name="demo",
            provider_status=TRANSFER_COMPLETED,
            proof=proof,
            raw_provider_reference="local-dev-demo-proof",
        )
        return SettlementProofResolution(
            proof=proof,
            transfer_record=record,
            transfer_summary=_transfer_record_summary(record),
        )

    if settlement_proof is not None:
        if not external_proof_enabled():
            raise SettlementProofError(
                "External settlementProof is disabled. Set ZALARY_USDCX_ALLOW_EXTERNAL_PROOF=true only when an external proof verifier is trusted."
            )
        proof = validate_settlement_proof(
            claim=claim,
            settlement_reference=settlement_reference,
            settlement_proof=settlement_proof,
        )
        record = _create_transfer_record_from_proof(
            claim=claim,
            settlement_reference=settlement_reference,
            provider_name="external",
            provider_status=TRANSFER_COMPLETED,
            proof=proof,
            raw_provider_reference=str(proof.get("transferInstructionCid") or proof.get("holdingCid") or ""),
        )
        return SettlementProofResolution(
            proof=proof,
            transfer_record=record,
            transfer_summary=_transfer_record_summary(record),
        )

    transfer_request = _token_transfer_request_for_claim(claim=claim, settlement_reference=settlement_reference)
    provider = get_token_transfer_provider()
    record = _create_transfer_record_for_request(transfer_request, provider_name=getattr(provider, "provider_name", ""))
    try:
        result = provider.execute_transfer(transfer_request)
    except Exception as exc:
        result = TokenTransferResult(
            status="failed",
            token=transfer_request.token,
            sender=transfer_request.sender_party,
            receiver=transfer_request.receiver_party,
            amount=transfer_request.amount,
            transferReference=transfer_request.transfer_reference,
            provider_name=getattr(provider, "provider_name", ""),
            error_message=safe_error_message(exc),
        )

    proof: dict[str, Any] = {}
    if result.status == TRANSFER_COMPLETED:
        proof = provider.build_token_transfer_proof(result)
        validate_settlement_proof(
            claim=claim,
            settlement_reference=settlement_reference,
            settlement_proof=proof,
        )

    _update_transfer_record_from_result(record, result=result, proof=proof)
    if result.status in {TRANSFER_PENDING, TRANSFER_PENDING_RECEIVER_ACCEPTANCE}:
        return SettlementProofResolution(
            proof={},
            transfer_record=record,
            transfer_summary=_transfer_record_summary(record),
            status=result.status,
            reason=(
                result.error_message
                or "Token transfer is pending; settlement confirmation will wait for a completed proof."
            ),
        )
    if result.status != TRANSFER_COMPLETED:
        raise SettlementProofError(
            result.error_message
            or f"Token transfer provider returned {result.status}; settlement cannot be confirmed."
        )

    return SettlementProofResolution(
        proof=proof,
        transfer_record=record,
        transfer_summary=_transfer_record_summary(record),
    )


def demo_settlement_proof(*, claim: SalaryClaimMirror, settlement_reference: str) -> dict[str, Any]:
    return {
        "token": (claim.payload or {}).get("claimToken") or {},
        "sender": claim.employer_wallet_party,
        "receiver": claim.employee_wallet_party,
        "amount": str(claim.claim_amount),
        "transferReference": (settlement_reference or "").strip(),
        "transferInstructionCid": None,
        "holdingCid": None,
        "executedAt": timezone.now().isoformat().replace("+00:00", "Z"),
    }


def _token_transfer_request_for_claim(*, claim: SalaryClaimMirror, settlement_reference: str) -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id=claim.company_id,
        payroll_id=claim.payroll_id,
        employee_external_id=claim.employee_external_id,
        salary_claim_contract_id=claim.contract_id,
        token=(claim.payload or {}).get("claimToken") or {},
        sender_party=claim.employer_wallet_party,
        receiver_party=claim.employee_wallet_party,
        amount=decimal_to_daml(claim.claim_amount),
        transfer_reference=(settlement_reference or "").strip(),
        metadata={
            "source_allocation_contract_id": claim.source_allocation_contract_id,
        },
    )


def _create_transfer_record_for_request(
    request: TokenTransferRequest,
    *,
    provider_name: str,
) -> USDCxTransferRecord:
    return USDCxTransferRecord.objects.create(
        company_id=request.company_id,
        payroll_id=request.payroll_id,
        employee_external_id=request.employee_external_id,
        salary_claim_contract_id=request.salary_claim_contract_id,
        settlement_reference=request.transfer_reference,
        provider_name=provider_name or "unavailable",
        provider_status="pending",
        sender_party=request.sender_party,
        receiver_party=request.receiver_party,
        amount=request.amount,
        token=_json_safe(request.token),
    )


def _create_transfer_record_from_proof(
    *,
    claim: SalaryClaimMirror,
    settlement_reference: str,
    provider_name: str,
    provider_status: str,
    proof: dict[str, Any],
    raw_provider_reference: str = "",
) -> USDCxTransferRecord:
    return USDCxTransferRecord.objects.create(
        company_id=claim.company_id,
        payroll_id=claim.payroll_id,
        employee_external_id=claim.employee_external_id,
        salary_claim_contract_id=claim.contract_id,
        settlement_reference=(settlement_reference or "").strip(),
        provider_name=provider_name,
        provider_status=provider_status,
        sender_party=claim.employer_wallet_party,
        receiver_party=claim.employee_wallet_party,
        amount=decimal_to_daml(claim.claim_amount),
        token=_json_safe((claim.payload or {}).get("claimToken") or {}),
        transfer_instruction_cid=str(proof.get("transferInstructionCid") or ""),
        holding_cid=str(proof.get("holdingCid") or ""),
        raw_provider_reference=(raw_provider_reference or "")[:1024],
        proof_payload=_json_safe(proof),
    )


def _update_transfer_record_from_result(
    record: USDCxTransferRecord,
    *,
    result: TokenTransferResult,
    proof: dict[str, Any],
) -> None:
    record.provider_name = result.provider_name or record.provider_name
    record.provider_status = result.status
    record.sender_party = result.sender or record.sender_party
    record.receiver_party = result.receiver or record.receiver_party
    if result.amount:
        record.amount = decimal_to_daml(result.amount)
    if result.token:
        record.token = _json_safe(result.token)
    record.transfer_instruction_cid = str(result.transferInstructionCid or "")
    record.holding_cid = str(result.holdingCid or "")
    record.raw_provider_reference = str(result.raw_provider_reference or "")[:1024]
    record.proof_payload = _json_safe(proof or result.proof_payload or {})
    record.error_message = safe_error_message(Exception(result.error_message)) if result.error_message else ""
    record.save(
        update_fields=[
            "provider_name",
            "provider_status",
            "sender_party",
            "receiver_party",
            "amount",
            "token",
            "transfer_instruction_cid",
            "holding_cid",
            "raw_provider_reference",
            "proof_payload",
            "error_message",
            "updated_at",
        ]
    )


def _transfer_record_summary(record: USDCxTransferRecord) -> dict[str, Any]:
    return {
        "provider": record.provider_name,
        "status": record.provider_status,
        "transfer_instruction_cid": record.transfer_instruction_cid,
        "holding_cid": record.holding_cid,
        "raw_provider_reference": record.raw_provider_reference,
        "error_message": record.error_message,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (Decimal,)):
        return decimal_to_daml(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def prepare_settlement_confirmation_payload(*, salary_claim_contract_id: str, proof: dict[str, Any]) -> dict[str, Any]:
    claim = _salary_claim_or_error(salary_claim_contract_id=salary_claim_contract_id)
    validated = validate_settlement_proof(
        claim=claim,
        settlement_reference=str(proof.get("transferReference") or ""),
        settlement_proof=proof,
    )
    vault = _active_payroll_vault_or_error(company_id=claim.company_id, payroll_id=claim.payroll_id)
    return _settlement_payload_or_error(
        payroll_vault_cid=vault.contract_id,
        settlement_reference=validated["transferReference"],
        settlement_proof=validated,
    )


def submit_settlement_confirmation(*, salary_claim_contract_id: str, proof: dict[str, Any]) -> SalaryExecutionResult:
    return confirm_salary_settlement(
        salary_claim_contract_id=salary_claim_contract_id,
        settlement_reference=str(proof.get("transferReference") or ""),
        settlement_proof=proof,
    )


def _claim_ticket_or_error(
    *,
    claim_ticket_contract_id: str | None,
    company_id: str | None,
    payroll_id: str | None,
    employee_external_id: str | None,
) -> ClaimTicketMirror:
    queryset = ClaimTicketMirror.objects.filter(ledger_active=True)
    if claim_ticket_contract_id:
        queryset = queryset.filter(contract_id=claim_ticket_contract_id.strip())
    else:
        _require_identifiers(company_id=company_id, payroll_id=payroll_id, employee_external_id=employee_external_id)
        queryset = queryset.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
            employee_external_id=(employee_external_id or "").strip(),
        )
    ticket = queryset.order_by("-synced_at").first()
    if ticket is None:
        raise OnboardingValidationError("ClaimTicket was not found in the local mirror.")
    return ticket


def _salary_claim_or_error(
    *,
    salary_claim_contract_id: str | None = None,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
) -> SalaryClaimMirror:
    queryset = SalaryClaimMirror.objects.all()
    if salary_claim_contract_id:
        queryset = queryset.filter(contract_id=salary_claim_contract_id.strip())
    else:
        _require_identifiers(company_id=company_id, payroll_id=payroll_id, employee_external_id=employee_external_id)
        queryset = queryset.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
            employee_external_id=(employee_external_id or "").strip(),
        ).exclude(claim_status=ARCHIVED_STATUS)
    claim = queryset.order_by("-synced_at").first()
    if claim is None:
        raise OnboardingValidationError("SalaryClaim was not found in the local mirror.")
    return claim


def _active_salary_claim_for_employee(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> SalaryClaimMirror | None:
    return (
        SalaryClaimMirror.objects.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
            employee_external_id=(employee_external_id or "").strip(),
            ledger_active=True,
        )
        .exclude(claim_status=ARCHIVED_STATUS)
        .order_by("-synced_at")
        .first()
    )


def _latest_salary_claim_for_employee(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> SalaryClaimMirror | None:
    return (
        SalaryClaimMirror.objects.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
            employee_external_id=(employee_external_id or "").strip(),
        )
        .order_by("-synced_at")
        .first()
    )


def _settlement_for_employee(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> SettlementReceiptMirror | SettledSalaryRecordMirror | None:
    filters = {
        "company_id": (company_id or "").strip(),
        "payroll_id": (payroll_id or "").strip(),
        "employee_external_id": (employee_external_id or "").strip(),
    }
    receipt = SettlementReceiptMirror.objects.filter(**filters).order_by("-synced_at").first()
    if receipt is not None:
        return receipt
    return SettledSalaryRecordMirror.objects.filter(**filters).order_by("-synced_at").first()


def _active_payroll_vault_or_error(*, company_id: str, payroll_id: str) -> PayrollVaultMirror:
    vault = (
        PayrollVaultMirror.objects.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
        )
        .exclude(vault_status=ARCHIVED_STATUS)
        .order_by("-synced_at")
        .first()
    )
    if vault is None:
        raise OnboardingValidationError("Active payroll vault was not found in the local mirror.")
    return vault


def _validate_claim_matches_vault(claim: SalaryClaimMirror, vault: PayrollVaultMirror) -> None:
    payload = claim.payload or {}
    if vault.hr_wallet_party != claim.hr_wallet_party:
        raise OnboardingValidationError("Payroll vault HR wallet does not match the salary claim.")
    if vault.employer_wallet_party != claim.employer_wallet_party:
        raise OnboardingValidationError("Payroll vault employer wallet does not match the salary claim.")
    if vault.company_id != claim.company_id or vault.payroll_id != claim.payroll_id:
        raise OnboardingValidationError("Payroll vault identifiers do not match the salary claim.")
    if vault.company_admin_party != claim.company_admin_party:
        raise OnboardingValidationError("Payroll vault company admin does not match the salary claim.")
    if not _same_token(vault.payroll_token or {}, payload.get("claimToken") or {}):
        raise OnboardingValidationError("Payroll vault token does not match the salary claim.")


def _validate_claim_source_allocation(claim: SalaryClaimMirror) -> None:
    allocation = SalaryAllocationMirror.objects.filter(
        contract_id=claim.source_allocation_contract_id,
    ).order_by("-synced_at").first()
    if allocation is None:
        raise OnboardingValidationError("Source SalaryAllocation was not found in the local mirror.")
    if allocation.allocation_status != ALLOCATION_TICKET_ISSUED_STATUS:
        raise OnboardingValidationError("Source SalaryAllocation must be AllocationClaimTicketIssued.")
    if allocation.company_id != claim.company_id:
        raise OnboardingValidationError("Source SalaryAllocation company ID does not match the salary claim.")
    if allocation.payroll_id != claim.payroll_id:
        raise OnboardingValidationError("Source SalaryAllocation payroll ID does not match the salary claim.")
    if allocation.employee_external_id != claim.employee_external_id:
        raise OnboardingValidationError("Source SalaryAllocation employeeExternalId does not match the salary claim.")
    if allocation.employee_wallet_party != claim.employee_wallet_party:
        raise OnboardingValidationError("Source SalaryAllocation employee wallet does not match the salary claim.")
    if allocation.employer_wallet_party != claim.employer_wallet_party:
        raise OnboardingValidationError("Source SalaryAllocation employer wallet does not match the salary claim.")
    if allocation.hr_wallet_party != claim.hr_wallet_party:
        raise OnboardingValidationError("Source SalaryAllocation HR wallet does not match the salary claim.")
    if allocation.company_admin_party != claim.company_admin_party:
        raise OnboardingValidationError("Source SalaryAllocation company admin does not match the salary claim.")
    if _decimal((allocation.salary_breakdown or {}).get("netPay")) != _decimal(claim.claim_amount):
        raise OnboardingValidationError("Source SalaryAllocation amount does not match the salary claim.")


def _settlement_payload_or_error(**kwargs: Any) -> dict[str, Any]:
    try:
        return confirm_salary_settlement_choice_payload(**kwargs)
    except ValueError as exc:
        raise SettlementProofError(str(exc)) from exc


def _require_identifiers(
    *,
    company_id: str | None,
    payroll_id: str | None,
    employee_external_id: str | None,
) -> None:
    if not (company_id and payroll_id and employee_external_id):
        raise OnboardingValidationError(
            "company_id, payroll_id, and employee_external_id are required when no contract id is provided."
        )


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError) as exc:
        raise SettlementProofError("Decimal value is invalid.") from exc


def _existing_salary_claim_result(
    claim: SalaryClaimMirror | None,
    *,
    ticket: ClaimTicketMirror | None = None,
    existing_contract: dict[str, Any] | None = None,
    reason: str,
) -> SalaryExecutionResult:
    if claim is not None:
        return SalaryExecutionResult(
            status="exists",
            action="RequestSalaryClaim",
            command_id="",
            update_id=None,
            company_id=claim.company_id,
            payroll_id=claim.payroll_id,
            employee_external_id=claim.employee_external_id,
            ledger_command_pk=0,
            contract_id=claim.contract_id,
            salary_claim_contract_id=claim.contract_id,
            choice_name=CHOICES["ClaimTicket"]["RequestSalaryClaim"],
            existing_contract=_salary_claim_summary(claim),
            reason=reason,
        )
    if ticket is None:
        raise OnboardingValidationError("ClaimTicket context is required for existing salary claim result.")
    return SalaryExecutionResult(
        status="exists",
        action="RequestSalaryClaim",
        command_id="",
        update_id=None,
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
        ledger_command_pk=0,
        contract_id=ticket.contract_id,
        choice_name=CHOICES["ClaimTicket"]["RequestSalaryClaim"],
        existing_contract=existing_contract,
        reason=reason,
    )


def _idempotent_salary_claim_result(*, ticket: ClaimTicketMirror, idempotent) -> SalaryExecutionResult:
    command = idempotent.command
    return SalaryExecutionResult(
        status=idempotent.status,
        action="RequestSalaryClaim",
        command_id=command.command_id,
        update_id=command.update_id or None,
        company_id=ticket.company_id,
        payroll_id=ticket.payroll_id,
        employee_external_id=ticket.employee_external_id,
        ledger_command_pk=command.pk,
        contract_id=ticket.contract_id,
        choice_name=CHOICES["ClaimTicket"]["RequestSalaryClaim"],
        reason="Equivalent ledger command already exists.",
    )


def _existing_settlement_result(
    *,
    claim: SalaryClaimMirror,
    settlement: SettlementReceiptMirror | SettledSalaryRecordMirror,
) -> SalaryExecutionResult:
    return SalaryExecutionResult(
        status="exists",
        action="ConfirmSalarySettlement",
        command_id="",
        update_id=None,
        company_id=claim.company_id,
        payroll_id=claim.payroll_id,
        employee_external_id=claim.employee_external_id,
        ledger_command_pk=0,
        contract_id=settlement.contract_id,
        salary_claim_contract_id=claim.contract_id,
        settlement_reference=getattr(settlement, "settlement_reference", ""),
        choice_name=CHOICES["SalaryClaim"]["ConfirmSalarySettlement"],
        existing_contract=_settlement_summary(settlement),
        reason="Salary settlement already exists.",
    )


def _idempotent_settlement_result(
    *,
    claim: SalaryClaimMirror,
    settlement_reference: str,
    idempotent,
) -> SalaryExecutionResult:
    command = idempotent.command
    return SalaryExecutionResult(
        status=idempotent.status,
        action="ConfirmSalarySettlement",
        command_id=command.command_id,
        update_id=command.update_id or None,
        company_id=claim.company_id,
        payroll_id=claim.payroll_id,
        employee_external_id=claim.employee_external_id,
        ledger_command_pk=command.pk,
        contract_id="",
        salary_claim_contract_id=claim.contract_id,
        settlement_reference=settlement_reference,
        choice_name=CHOICES["SalaryClaim"]["ConfirmSalarySettlement"],
        reason="Equivalent ledger command already exists.",
    )


def _salary_claim_summary(claim: SalaryClaimMirror) -> dict[str, Any]:
    return {
        "contract_id": claim.contract_id,
        "company_id": claim.company_id,
        "payroll_id": claim.payroll_id,
        "employee_external_id": claim.employee_external_id,
        "claim_status": claim.claim_status,
        "claim_amount": str(claim.claim_amount),
        "source_allocation_contract_id": claim.source_allocation_contract_id,
    }


def _settlement_summary(settlement: SettlementReceiptMirror | SettledSalaryRecordMirror) -> dict[str, Any]:
    return {
        "contract_id": settlement.contract_id,
        "company_id": settlement.company_id,
        "payroll_id": settlement.payroll_id,
        "employee_external_id": settlement.employee_external_id,
        "settlement_reference": getattr(settlement, "settlement_reference", ""),
        "amount": str(getattr(settlement, "amount", "")),
    }
