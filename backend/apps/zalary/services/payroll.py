from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import os
from typing import Any
from uuid import uuid4

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.zalary.models import (
    ClaimTicketMirror,
    CommandStatus,
    CompanyMirror,
    EmployeeEnrollmentMirror,
    LedgerCommand,
    PayrollVaultMirror,
    SalaryAllocationMirror,
)

from .auth import COMMAND_ID_PREFIX, default_read_parties, load_ledger_auth_settings
from .errors import (
    DuplicateClaimTicketError,
    DuplicatePayrollVaultError,
    DuplicateSalaryAllocationError,
    LedgerSubmissionError,
    OnboardingValidationError,
    safe_error_message,
)
from .ledger import CommandContext, LedgerClient
from .payloads import (
    activate_payroll_choice_payload,
    add_salary_allocation_choice_payload,
    confirm_funding_choice_payload,
    create_payroll_vault_choice_payload,
    decimal_to_daml,
    finalize_allocations_choice_payload,
    issue_claim_ticket_choice_payload,
    salary_breakdown_payload,
)
from .sync import (
    ClaimTicketSyncResult,
    FundingReceiptSyncResult,
    PayrollVaultSyncResult,
    SalaryAllocationSyncResult,
    sync_claim_tickets,
    sync_funding_receipts,
    sync_payroll_vaults,
    sync_salary_allocations,
)
from .templates import CHOICES, CLAIM_TICKET, COMPANY, PAYROLL_VAULT, SALARY_ALLOCATION, TemplateRef


ARCHIVED_STATUS = "Archived"
FINALIZED_STATUS = "AllocationsFinalized"
FUNDED_STATUS = "Funded"
ACTIVE_STATUS = "Active"
CREATED_STATUS = "Created"
ALLOCATION_CREATED_STATUS = "AllocationCreated"
ALLOCATION_TICKET_ISSUED_STATUS = "AllocationClaimTicketIssued"
PENDING_CLAIM_WINDOW_OPEN_STATUS = "pending_claim_window_open"


@dataclass(frozen=True)
class PayrollVaultPreflightResult:
    status: str
    company_id: str
    company_name: str
    payroll_id: str
    hr_wallet: str
    employer_wallet: str
    payroll_token: dict[str, Any]
    expected_employee_count: int
    act_as: list[str]
    future_command: dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "payroll_id": self.payroll_id,
            "hr_wallet": self.hr_wallet,
            "employer_wallet": self.employer_wallet,
            "payroll_token": self.payroll_token,
            "expected_employee_count": self.expected_employee_count,
            "act_as": self.act_as,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class SalaryAllocationPreflightResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    allocation_employee_wallet: str
    enrollment_contract_id: str
    payroll_vault_contract_id: str
    hr_wallet: str
    employer_wallet: str
    act_as: list[str]
    future_command: dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "allocation_employee_wallet": self.allocation_employee_wallet,
            "enrollment_contract_id": self.enrollment_contract_id,
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "hr_wallet": self.hr_wallet,
            "employer_wallet": self.employer_wallet,
            "act_as": self.act_as,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class FinalizeAllocationsPreflightResult:
    status: str
    company_id: str
    payroll_id: str
    payroll_vault_contract_id: str
    uploaded_allocation_count: int
    expected_employee_count: int
    hr_wallet: str
    act_as: list[str]
    future_command: dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "uploaded_allocation_count": self.uploaded_allocation_count,
            "expected_employee_count": self.expected_employee_count,
            "hr_wallet": self.hr_wallet,
            "act_as": self.act_as,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class ConfirmFundingPreflightResult:
    status: str
    company_id: str
    payroll_id: str
    payroll_vault_contract_id: str
    funding_amount: str
    funding_reference: str
    employer_wallet: str
    act_as: list[str]
    future_command: dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "funding_amount": self.funding_amount,
            "funding_reference": self.funding_reference,
            "employer_wallet": self.employer_wallet,
            "act_as": self.act_as,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class ActivatePayrollPreflightResult:
    status: str
    company_id: str
    payroll_id: str
    payroll_vault_contract_id: str
    employer_wallet: str
    act_as: list[str]
    future_command: dict[str, Any]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "employer_wallet": self.employer_wallet,
            "act_as": self.act_as,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class IssueClaimTicketPreflightResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    payroll_vault_contract_id: str
    salary_allocation_contract_id: str
    hr_wallet: str
    act_as: list[str]
    claim_window_start: str
    claim_window_end: str
    future_command: dict[str, Any]
    reason: str = ""

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "salary_allocation_contract_id": self.salary_allocation_contract_id,
            "hr_wallet": self.hr_wallet,
            "act_as": self.act_as,
            "claim_window_start": self.claim_window_start,
            "claim_window_end": self.claim_window_end,
            "future_command": self.future_command,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PayrollCommandStepResult:
    status: str
    action: str
    command_id: str
    update_id: str | None
    company_id: str
    payroll_id: str
    ledger_command_pk: int
    contract_id: str = ""
    choice_name: str = ""
    synced_payroll_vaults: PayrollVaultSyncResult | None = None
    synced_salary_allocations: SalaryAllocationSyncResult | None = None
    synced_funding_receipts: FundingReceiptSyncResult | None = None
    synced_claim_tickets: ClaimTicketSyncResult | None = None
    existing_contract: dict[str, Any] | None = None
    reason: str = ""
    salary_allocation_contract_id: str = ""
    claim_window_start: str = ""
    claim_window_end: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "status": self.status,
            "action": self.action,
            "command_id": self.command_id,
            "update_id": self.update_id,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "ledger_command_id": self.ledger_command_pk,
            "contract_id": self.contract_id,
            "choice_name": self.choice_name,
        }
        if self.synced_payroll_vaults is not None:
            summary["synced_payroll_vaults"] = self.synced_payroll_vaults.safe_summary()
        if self.synced_salary_allocations is not None:
            summary["synced_salary_allocations"] = self.synced_salary_allocations.safe_summary()
        if self.synced_funding_receipts is not None:
            summary["synced_funding_receipts"] = self.synced_funding_receipts.safe_summary()
        if self.synced_claim_tickets is not None:
            summary["synced_claim_tickets"] = self.synced_claim_tickets.safe_summary()
        if self.existing_contract is not None:
            summary["existing_contract"] = self.existing_contract
        if self.salary_allocation_contract_id:
            summary["salary_allocation_contract_id"] = self.salary_allocation_contract_id
        if self.reason:
            summary["reason"] = self.reason
        if self.claim_window_start or self.claim_window_end:
            summary["claim_window_start"] = self.claim_window_start
            summary["claim_window_end"] = self.claim_window_end
        return summary


@dataclass(frozen=True)
class DemoPayrollPipelinePreflightResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    steps: list[dict[str, Any]]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "steps": self.steps,
        }


@dataclass(frozen=True)
class DemoPayrollPipelineResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    steps: list[PayrollCommandStepResult]
    payroll_vault_contract_id: str
    salary_allocation_contract_id: str
    payroll_vault_status: str

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "salary_allocation_contract_id": self.salary_allocation_contract_id,
            "payroll_vault_status": self.payroll_vault_status,
            "payroll_vault_finalized": self.payroll_vault_status == FINALIZED_STATUS,
            "steps": [step.safe_summary() for step in self.steps],
        }


@dataclass(frozen=True)
class DemoFundingTicketPipelinePreflightResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    steps: list[dict[str, Any]]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "steps": self.steps,
        }


@dataclass(frozen=True)
class DemoFundingTicketPipelineResult:
    status: str
    company_id: str
    payroll_id: str
    employee_external_id: str
    steps: list[PayrollCommandStepResult]
    payroll_vault_contract_id: str
    salary_allocation_contract_id: str
    claim_ticket_contract_id: str
    payroll_vault_status: str
    salary_allocation_status: str

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "payroll_id": self.payroll_id,
            "employee_external_id": self.employee_external_id,
            "funding_status": self.steps[0].status if len(self.steps) > 0 else "",
            "activation_status": self.steps[1].status if len(self.steps) > 1 else "",
            "claim_ticket_status": self.steps[2].status if len(self.steps) > 2 else "",
            "payroll_vault_contract_id": self.payroll_vault_contract_id,
            "salary_allocation_contract_id": self.salary_allocation_contract_id,
            "claim_ticket_contract_id": self.claim_ticket_contract_id,
            "payroll_vault_status": self.payroll_vault_status,
            "salary_allocation_status": self.salary_allocation_status,
            "steps": [step.safe_summary() for step in self.steps],
        }


def preflight_payroll_vault_creation(
    *,
    company_id: str,
    hr_wallet: str,
    employer_wallet: str,
    payroll_id: str,
    payroll_period: dict[str, Any],
    payroll_token: dict[str, Any],
    claim_window_start: Any,
    claim_window_end: Any,
    expected_employee_count: int,
) -> PayrollVaultPreflightResult:
    company = _company_or_error(company_id)
    payload = _payroll_vault_payload_or_error(
        hr_wallet=hr_wallet,
        employer_wallet=employer_wallet,
        payroll_id=payroll_id,
        payroll_period=payroll_period,
        payroll_token=payroll_token,
        claim_window_start=claim_window_start,
        claim_window_end=claim_window_end,
        expected_employee_count=expected_employee_count,
    )

    if payload["hrWallet"] not in (company.hr_wallet_parties or []):
        raise OnboardingValidationError("hrWallet is not authorized for this company.")
    if payload["employerWallet"] not in (company.employer_wallet_parties or []):
        raise OnboardingValidationError("employerWallet is not authorized for this company.")
    if not _token_in_list(payload["payrollToken"], company.allowed_tokens or []):
        raise OnboardingValidationError("payrollToken is not allowed for this company.")
    if _active_payroll_vault(company_id=company.company_id, payroll_id=payload["payrollId"]) is not None:
        raise DuplicatePayrollVaultError(
            f"Payroll vault already exists locally for {company.company_id}/{payload['payrollId']}."
        )

    _validate_period(payload["payrollPeriod"])
    _validate_claim_window(payload["claimWindowStart"], payload["claimWindowEnd"])
    return PayrollVaultPreflightResult(
        status="ok",
        company_id=company.company_id,
        company_name=company.company_name,
        payroll_id=payload["payrollId"],
        hr_wallet=payload["hrWallet"],
        employer_wallet=payload["employerWallet"],
        payroll_token=payload["payrollToken"],
        expected_employee_count=int(payload["expectedEmployeeCount"]),
        act_as=[payload["hrWallet"]],
        future_command={
            "template": COMPANY.display_id(),
            "choice": CHOICES["Company"]["CreatePayrollVault"],
            "contract_id": company.contract_id,
        },
    )


def create_payroll_vault(
    *,
    company_id: str,
    hr_wallet: str,
    employer_wallet: str,
    payroll_id: str,
    payroll_period: dict[str, Any],
    payroll_token: dict[str, Any],
    claim_window_start: Any,
    claim_window_end: Any,
    expected_employee_count: int,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> PayrollCommandStepResult:
    existing = _active_payroll_vault(company_id=company_id, payroll_id=payroll_id)
    if existing is not None:
        if allow_existing:
            return _existing_step(
                action="CreatePayrollVault",
                company_id=existing.company_id,
                payroll_id=existing.payroll_id,
                choice_name=CHOICES["Company"]["CreatePayrollVault"],
                contract_id=existing.contract_id,
                existing_contract=_payroll_vault_summary(existing),
            )
        raise DuplicatePayrollVaultError(f"Payroll vault already exists locally for {company_id}/{payroll_id}.")

    preflight = preflight_payroll_vault_creation(
        company_id=company_id,
        hr_wallet=hr_wallet,
        employer_wallet=employer_wallet,
        payroll_id=payroll_id,
        payroll_period=payroll_period,
        payroll_token=payroll_token,
        claim_window_start=claim_window_start,
        claim_window_end=claim_window_end,
        expected_employee_count=expected_employee_count,
    )
    payload = _payroll_vault_payload_or_error(
        hr_wallet=preflight.hr_wallet,
        employer_wallet=preflight.employer_wallet,
        payroll_id=preflight.payroll_id,
        payroll_period=payroll_period,
        payroll_token=preflight.payroll_token,
        claim_window_start=claim_window_start,
        claim_window_end=claim_window_end,
        expected_employee_count=preflight.expected_employee_count,
    )
    command_result = _submit_choice(
        action_slug="create-payroll-vault",
        workflow_id=f"zalary-create-payroll-vault-{preflight.company_id}-{preflight.payroll_id}",
        act_as=preflight.act_as,
        template=COMPANY,
        contract_id=preflight.future_command["contract_id"],
        choice=CHOICES["Company"]["CreatePayrollVault"],
        payload=payload,
    )

    synced_payroll_vaults = None
    contract_id = ""
    if sync_after:
        synced_payroll_vaults = sync_payroll_vaults(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        vault = _active_payroll_vault(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        contract_id = vault.contract_id if vault is not None else ""

    return PayrollCommandStepResult(
        status="ok",
        action="CreatePayrollVault",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=contract_id,
        choice_name=CHOICES["Company"]["CreatePayrollVault"],
        synced_payroll_vaults=synced_payroll_vaults,
        raw_response=command_result["result"].raw_response,
    )


def preflight_salary_allocation(
    *,
    company_id: str,
    payroll_id: str,
    allocation_employee_wallet: str,
    employee_external_id: str,
    salary_breakdown: dict[str, Any],
    enrollment_cid: str,
) -> SalaryAllocationPreflightResult:
    vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    if vault.vault_status != CREATED_STATUS:
        raise OnboardingValidationError("Payroll vault must be in Created status before adding allocations.")

    enrollment = _enrollment_or_error(
        company_id=company_id,
        employee_external_id=employee_external_id,
        enrollment_cid=enrollment_cid,
    )
    payload = _salary_allocation_payload_or_error(
        allocation_employee_wallet=allocation_employee_wallet,
        employee_external_id=employee_external_id,
        salary_breakdown=salary_breakdown,
        enrollment_cid=enrollment.contract_id,
    )
    _validate_salary_breakdown(payload["salaryBreakdown"])

    if enrollment.employee_wallet_party != payload["allocationEmployeeWallet"]:
        raise OnboardingValidationError("allocationEmployeeWallet must match the employee enrollment wallet.")
    if enrollment.employee_external_id != payload["employeeExternalId"]:
        raise OnboardingValidationError("employeeExternalId must match the employee enrollment.")
    if enrollment.hr_wallet_party != vault.hr_wallet_party:
        raise OnboardingValidationError("Enrollment HR wallet does not match the payroll vault.")
    if enrollment.employer_wallet_party != vault.employer_wallet_party:
        raise OnboardingValidationError("Enrollment employer wallet does not match the payroll vault.")
    if enrollment.company_admin_party != vault.company_admin_party:
        raise OnboardingValidationError("Enrollment company admin does not match the payroll vault.")
    if not _same_token(payload["salaryBreakdown"]["token"], vault.payroll_token or {}):
        raise OnboardingValidationError("salaryBreakdown token must match payrollToken.")
    if _active_salary_allocation(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=payload["employeeExternalId"],
    ) is not None:
        raise DuplicateSalaryAllocationError(
            f"Salary allocation already exists locally for {company_id}/{payroll_id}/{payload['employeeExternalId']}."
        )
    if _uploaded_count(vault) >= _expected_count(vault):
        raise OnboardingValidationError("Uploaded allocation count cannot exceed expected employee count.")

    return SalaryAllocationPreflightResult(
        status="ok",
        company_id=vault.company_id,
        payroll_id=vault.payroll_id,
        employee_external_id=payload["employeeExternalId"],
        allocation_employee_wallet=payload["allocationEmployeeWallet"],
        enrollment_contract_id=enrollment.contract_id,
        payroll_vault_contract_id=vault.contract_id,
        hr_wallet=vault.hr_wallet_party,
        employer_wallet=vault.employer_wallet_party,
        act_as=[vault.hr_wallet_party],
        future_command={
            "template": PAYROLL_VAULT.display_id(),
            "choice": CHOICES["PayrollVault"]["AddSalaryAllocation"],
            "contract_id": vault.contract_id,
        },
    )


def add_salary_allocation(
    *,
    company_id: str,
    payroll_id: str,
    allocation_employee_wallet: str,
    employee_external_id: str,
    salary_breakdown: dict[str, Any],
    enrollment_cid: str,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> PayrollCommandStepResult:
    existing = _active_salary_allocation(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if existing is not None:
        if allow_existing:
            return _existing_step(
                action="AddSalaryAllocation",
                company_id=existing.company_id,
                payroll_id=existing.payroll_id,
                choice_name=CHOICES["PayrollVault"]["AddSalaryAllocation"],
                contract_id=existing.contract_id,
                existing_contract=_salary_allocation_summary(existing),
            )
        raise DuplicateSalaryAllocationError(
            f"Salary allocation already exists locally for {company_id}/{payroll_id}/{employee_external_id}."
        )

    preflight = preflight_salary_allocation(
        company_id=company_id,
        payroll_id=payroll_id,
        allocation_employee_wallet=allocation_employee_wallet,
        employee_external_id=employee_external_id,
        salary_breakdown=salary_breakdown,
        enrollment_cid=enrollment_cid,
    )
    payload = _salary_allocation_payload_or_error(
        allocation_employee_wallet=preflight.allocation_employee_wallet,
        employee_external_id=preflight.employee_external_id,
        salary_breakdown=salary_breakdown,
        enrollment_cid=preflight.enrollment_contract_id,
    )
    command_result = _submit_choice(
        action_slug="add-salary-allocation",
        workflow_id=f"zalary-add-salary-allocation-{preflight.company_id}-{preflight.payroll_id}-{preflight.employee_external_id}",
        act_as=preflight.act_as,
        template=PAYROLL_VAULT,
        contract_id=preflight.payroll_vault_contract_id,
        choice=CHOICES["PayrollVault"]["AddSalaryAllocation"],
        payload=payload,
    )

    synced_payroll_vaults = None
    synced_salary_allocations = None
    contract_id = ""
    if sync_after:
        synced_payroll_vaults = sync_payroll_vaults(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        synced_salary_allocations = sync_salary_allocations(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        allocation = _active_salary_allocation(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        contract_id = allocation.contract_id if allocation is not None else ""

    return PayrollCommandStepResult(
        status="ok",
        action="AddSalaryAllocation",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=contract_id,
        choice_name=CHOICES["PayrollVault"]["AddSalaryAllocation"],
        synced_payroll_vaults=synced_payroll_vaults,
        synced_salary_allocations=synced_salary_allocations,
        raw_response=command_result["result"].raw_response,
    )


def preflight_finalize_allocations(*, company_id: str, payroll_id: str) -> FinalizeAllocationsPreflightResult:
    vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    if vault.vault_status != CREATED_STATUS:
        raise OnboardingValidationError("Payroll vault must be in Created status before finalizing allocations.")
    uploaded_count = _uploaded_count(vault)
    expected_count = _expected_count(vault)
    if uploaded_count != expected_count:
        raise OnboardingValidationError("Uploaded allocation count must equal expected employee count before finalizing.")
    if _decimal(vault.totals.get("totalNetPay")) <= Decimal("0"):
        raise OnboardingValidationError("Total net pay must be greater than zero before finalizing.")
    return FinalizeAllocationsPreflightResult(
        status="ok",
        company_id=vault.company_id,
        payroll_id=vault.payroll_id,
        payroll_vault_contract_id=vault.contract_id,
        uploaded_allocation_count=uploaded_count,
        expected_employee_count=expected_count,
        hr_wallet=vault.hr_wallet_party,
        act_as=[vault.hr_wallet_party],
        future_command={
            "template": PAYROLL_VAULT.display_id(),
            "choice": CHOICES["PayrollVault"]["FinalizeAllocations"],
            "contract_id": vault.contract_id,
        },
    )


def finalize_allocations(
    *,
    company_id: str,
    payroll_id: str,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> PayrollCommandStepResult:
    existing = _active_payroll_vault(company_id=company_id, payroll_id=payroll_id)
    if existing is not None and existing.vault_status == FINALIZED_STATUS:
        if allow_existing:
            return _existing_step(
                action="FinalizeAllocations",
                company_id=existing.company_id,
                payroll_id=existing.payroll_id,
                choice_name=CHOICES["PayrollVault"]["FinalizeAllocations"],
                contract_id=existing.contract_id,
                existing_contract=_payroll_vault_summary(existing),
            )
        raise OnboardingValidationError("Payroll vault allocations are already finalized.")

    preflight = preflight_finalize_allocations(company_id=company_id, payroll_id=payroll_id)
    payload = finalize_allocations_choice_payload()
    command_result = _submit_choice(
        action_slug="finalize-allocations",
        workflow_id=f"zalary-finalize-allocations-{preflight.company_id}-{preflight.payroll_id}",
        act_as=preflight.act_as,
        template=PAYROLL_VAULT,
        contract_id=preflight.payroll_vault_contract_id,
        choice=CHOICES["PayrollVault"]["FinalizeAllocations"],
        payload=payload,
    )

    synced_payroll_vaults = None
    contract_id = ""
    if sync_after:
        synced_payroll_vaults = sync_payroll_vaults(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        vault = _active_payroll_vault(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        contract_id = vault.contract_id if vault is not None else ""

    return PayrollCommandStepResult(
        status="ok",
        action="FinalizeAllocations",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=contract_id,
        choice_name=CHOICES["PayrollVault"]["FinalizeAllocations"],
        synced_payroll_vaults=synced_payroll_vaults,
        raw_response=command_result["result"].raw_response,
    )


def preflight_confirm_funding(
    *,
    company_id: str,
    payroll_id: str,
    funding_amount: Decimal | str,
    funding_reference: str,
    funding_proof: dict[str, Any] | None = None,
) -> ConfirmFundingPreflightResult:
    vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    if vault.vault_status != FINALIZED_STATUS:
        raise OnboardingValidationError("Payroll vault must be AllocationsFinalized before confirming funding.")
    payload = _confirm_funding_payload_or_error(
        funding_amount=funding_amount,
        funding_reference=funding_reference,
        funding_proof=funding_proof,
    )
    funding_decimal = _decimal(payload["fundingAmount"])
    if funding_decimal <= Decimal("0"):
        raise OnboardingValidationError("fundingAmount must be positive.")
    if funding_decimal < _total_net_pay(vault):
        raise OnboardingValidationError("fundingAmount must cover totalNetPay.")

    return ConfirmFundingPreflightResult(
        status="ok",
        company_id=vault.company_id,
        payroll_id=vault.payroll_id,
        payroll_vault_contract_id=vault.contract_id,
        funding_amount=payload["fundingAmount"],
        funding_reference=payload["fundingReference"],
        employer_wallet=vault.employer_wallet_party,
        act_as=[vault.employer_wallet_party],
        future_command={
            "template": PAYROLL_VAULT.display_id(),
            "choice": CHOICES["PayrollVault"]["ConfirmFunding"],
            "contract_id": vault.contract_id,
        },
    )


def confirm_funding(
    *,
    company_id: str,
    payroll_id: str,
    funding_amount: Decimal | str,
    funding_reference: str,
    funding_proof: dict[str, Any] | None = None,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> PayrollCommandStepResult:
    existing = _active_payroll_vault(company_id=company_id, payroll_id=payroll_id)
    if existing is not None and existing.vault_status in {FUNDED_STATUS, ACTIVE_STATUS}:
        return _existing_step(
            action="ConfirmFunding",
            company_id=existing.company_id,
            payroll_id=existing.payroll_id,
            choice_name=CHOICES["PayrollVault"]["ConfirmFunding"],
            contract_id=existing.contract_id,
            existing_contract=_payroll_vault_summary(existing),
            reason="Payroll vault is already funded or active.",
        )

    preflight = preflight_confirm_funding(
        company_id=company_id,
        payroll_id=payroll_id,
        funding_amount=funding_amount,
        funding_reference=funding_reference,
        funding_proof=funding_proof,
    )
    payload = _confirm_funding_payload_or_error(
        funding_amount=preflight.funding_amount,
        funding_reference=preflight.funding_reference,
        funding_proof=funding_proof,
    )
    command_result = _submit_choice(
        action_slug="confirm-funding",
        workflow_id=f"zalary-confirm-funding-{preflight.company_id}-{preflight.payroll_id}",
        act_as=preflight.act_as,
        template=PAYROLL_VAULT,
        contract_id=preflight.payroll_vault_contract_id,
        choice=CHOICES["PayrollVault"]["ConfirmFunding"],
        payload=payload,
    )

    synced_payroll_vaults = None
    synced_funding_receipts = None
    contract_id = ""
    if sync_after:
        synced_funding_receipts = sync_funding_receipts(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        synced_payroll_vaults = sync_payroll_vaults(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        vault = _active_payroll_vault(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        contract_id = vault.contract_id if vault is not None else ""

    return PayrollCommandStepResult(
        status="ok",
        action="ConfirmFunding",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=contract_id,
        choice_name=CHOICES["PayrollVault"]["ConfirmFunding"],
        synced_payroll_vaults=synced_payroll_vaults,
        synced_funding_receipts=synced_funding_receipts,
        raw_response=command_result["result"].raw_response,
    )


def preflight_activate_payroll(*, company_id: str, payroll_id: str) -> ActivatePayrollPreflightResult:
    vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    if vault.vault_status != FUNDED_STATUS:
        raise OnboardingValidationError("Payroll vault must be Funded before activation.")
    return ActivatePayrollPreflightResult(
        status="ok",
        company_id=vault.company_id,
        payroll_id=vault.payroll_id,
        payroll_vault_contract_id=vault.contract_id,
        employer_wallet=vault.employer_wallet_party,
        act_as=[vault.employer_wallet_party],
        future_command={
            "template": PAYROLL_VAULT.display_id(),
            "choice": CHOICES["PayrollVault"]["ActivatePayroll"],
            "contract_id": vault.contract_id,
        },
    )


def activate_payroll(
    *,
    company_id: str,
    payroll_id: str,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> PayrollCommandStepResult:
    existing = _active_payroll_vault(company_id=company_id, payroll_id=payroll_id)
    if existing is not None and existing.vault_status == ACTIVE_STATUS:
        return _existing_step(
            action="ActivatePayroll",
            company_id=existing.company_id,
            payroll_id=existing.payroll_id,
            choice_name=CHOICES["PayrollVault"]["ActivatePayroll"],
            contract_id=existing.contract_id,
            existing_contract=_payroll_vault_summary(existing),
            reason="Payroll vault is already active.",
        )

    preflight = preflight_activate_payroll(company_id=company_id, payroll_id=payroll_id)
    payload = activate_payroll_choice_payload()
    command_result = _submit_choice(
        action_slug="activate-payroll",
        workflow_id=f"zalary-activate-payroll-{preflight.company_id}-{preflight.payroll_id}",
        act_as=preflight.act_as,
        template=PAYROLL_VAULT,
        contract_id=preflight.payroll_vault_contract_id,
        choice=CHOICES["PayrollVault"]["ActivatePayroll"],
        payload=payload,
    )

    synced_payroll_vaults = None
    contract_id = ""
    if sync_after:
        synced_payroll_vaults = sync_payroll_vaults(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        vault = _active_payroll_vault(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        contract_id = vault.contract_id if vault is not None else ""

    return PayrollCommandStepResult(
        status="ok",
        action="ActivatePayroll",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=contract_id,
        choice_name=CHOICES["PayrollVault"]["ActivatePayroll"],
        synced_payroll_vaults=synced_payroll_vaults,
        raw_response=command_result["result"].raw_response,
    )


def preflight_issue_claim_ticket(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> IssueClaimTicketPreflightResult:
    vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    allocation = _active_salary_allocation_or_error(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if _claim_ticket_for_employee(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    ) is not None:
        raise DuplicateClaimTicketError(
            f"Claim ticket already exists locally for {company_id}/{payroll_id}/{employee_external_id}."
        )
    if vault.vault_status != ACTIVE_STATUS:
        raise OnboardingValidationError("Payroll vault must be Active before issuing claim tickets.")
    if allocation.allocation_status != ALLOCATION_CREATED_STATUS:
        raise OnboardingValidationError("SalaryAllocation must be AllocationCreated before issuing a claim ticket.")
    _validate_allocation_matches_vault(allocation, vault)

    now = timezone.now()
    claim_window_start = _aware_datetime_or_error(vault.claim_window_start, "claimWindowStart")
    claim_window_end = _aware_datetime_or_error(vault.claim_window_end, "claimWindowEnd")
    status = "ok"
    reason = ""
    if now < claim_window_start:
        status = PENDING_CLAIM_WINDOW_OPEN_STATUS
        reason = "Claim window has not opened."
    elif now > claim_window_end:
        raise OnboardingValidationError("Claim window has expired.")

    return IssueClaimTicketPreflightResult(
        status=status,
        company_id=vault.company_id,
        payroll_id=vault.payroll_id,
        employee_external_id=allocation.employee_external_id,
        payroll_vault_contract_id=vault.contract_id,
        salary_allocation_contract_id=allocation.contract_id,
        hr_wallet=allocation.hr_wallet_party,
        act_as=[allocation.hr_wallet_party],
        claim_window_start=claim_window_start.isoformat(),
        claim_window_end=claim_window_end.isoformat(),
        reason=reason,
        future_command={
            "template": SALARY_ALLOCATION.display_id(),
            "choice": CHOICES["SalaryAllocation"]["IssueClaimTicket"],
            "contract_id": allocation.contract_id,
        },
    )


def issue_claim_ticket(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> PayrollCommandStepResult:
    existing_ticket = _claim_ticket_for_employee(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if existing_ticket is not None:
        if allow_existing:
            allocation = _active_salary_allocation(
                company_id=company_id,
                payroll_id=payroll_id,
                employee_external_id=employee_external_id,
            )
            return _existing_step(
                action="IssueClaimTicket",
                company_id=existing_ticket.company_id,
                payroll_id=existing_ticket.payroll_id,
                choice_name=CHOICES["SalaryAllocation"]["IssueClaimTicket"],
                contract_id=existing_ticket.contract_id,
                existing_contract=_claim_ticket_summary(existing_ticket),
                salary_allocation_contract_id=allocation.contract_id if allocation is not None else "",
                reason="Claim ticket already exists.",
            )
        raise DuplicateClaimTicketError(
            f"Claim ticket already exists locally for {company_id}/{payroll_id}/{employee_external_id}."
        )

    preflight = preflight_issue_claim_ticket(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if preflight.status == PENDING_CLAIM_WINDOW_OPEN_STATUS:
        return PayrollCommandStepResult(
            status=PENDING_CLAIM_WINDOW_OPEN_STATUS,
            action="IssueClaimTicket",
            command_id="",
            update_id=None,
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            ledger_command_pk=0,
            contract_id="",
            choice_name=CHOICES["SalaryAllocation"]["IssueClaimTicket"],
            salary_allocation_contract_id=preflight.salary_allocation_contract_id,
            reason=preflight.reason,
            claim_window_start=preflight.claim_window_start,
            claim_window_end=preflight.claim_window_end,
        )

    payload = issue_claim_ticket_choice_payload(payrollVaultCid=preflight.payroll_vault_contract_id)
    command_result = _submit_choice(
        action_slug="issue-claim-ticket",
        workflow_id=f"zalary-issue-claim-ticket-{preflight.company_id}-{preflight.payroll_id}-{preflight.employee_external_id}",
        act_as=preflight.act_as,
        template=SALARY_ALLOCATION,
        contract_id=preflight.salary_allocation_contract_id,
        choice=CHOICES["SalaryAllocation"]["IssueClaimTicket"],
        payload=payload,
    )

    synced_salary_allocations = None
    synced_claim_tickets = None
    contract_id = ""
    salary_allocation_contract_id = ""
    if sync_after:
        synced_salary_allocations = sync_salary_allocations(company_id=preflight.company_id, payroll_id=preflight.payroll_id)
        synced_claim_tickets = sync_claim_tickets(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        ticket = _claim_ticket_for_employee(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        allocation = _active_salary_allocation(
            company_id=preflight.company_id,
            payroll_id=preflight.payroll_id,
            employee_external_id=preflight.employee_external_id,
        )
        contract_id = ticket.contract_id if ticket is not None else ""
        salary_allocation_contract_id = allocation.contract_id if allocation is not None else ""

    return PayrollCommandStepResult(
        status="ok",
        action="IssueClaimTicket",
        command_id=command_result["command"].command_id,
        update_id=command_result["result"].update_id,
        company_id=preflight.company_id,
        payroll_id=preflight.payroll_id,
        ledger_command_pk=command_result["command"].pk,
        contract_id=contract_id,
        choice_name=CHOICES["SalaryAllocation"]["IssueClaimTicket"],
        synced_salary_allocations=synced_salary_allocations,
        synced_claim_tickets=synced_claim_tickets,
        salary_allocation_contract_id=salary_allocation_contract_id,
        raw_response=command_result["result"].raw_response,
    )


def preflight_demo_payroll_pipeline(
    *,
    company_id: str,
    employee_external_id: str = "EMP-001",
    payroll_id: str | None = None,
    gross_pay: Decimal | str = "1000",
    allowances: Decimal | str = "200",
    deductions: Decimal | str = "100",
    net_pay: Decimal | str = "1100",
) -> DemoPayrollPipelinePreflightResult:
    inputs = _demo_pipeline_inputs(
        company_id=company_id,
        employee_external_id=employee_external_id,
        payroll_id=payroll_id,
        gross_pay=gross_pay,
        allowances=allowances,
        deductions=deductions,
        net_pay=net_pay,
    )
    vault_preflight = preflight_payroll_vault_creation(**inputs["vault"])
    _validate_demo_allocation_inputs(inputs)
    return DemoPayrollPipelinePreflightResult(
        status="ok",
        company_id=inputs["company"].company_id,
        payroll_id=inputs["vault"]["payroll_id"],
        employee_external_id=inputs["employee_external_id"],
        steps=[
            vault_preflight.safe_summary(),
            {
                "status": "ok",
                "template": PAYROLL_VAULT.display_id(),
                "choice": CHOICES["PayrollVault"]["AddSalaryAllocation"],
                "employee_external_id": inputs["employee_external_id"],
                "enrollment_contract_id": inputs["enrollment"].contract_id,
            },
            {
                "status": "ok",
                "template": PAYROLL_VAULT.display_id(),
                "choice": CHOICES["PayrollVault"]["FinalizeAllocations"],
            },
        ],
    )


def create_demo_payroll_pipeline(
    *,
    company_id: str,
    employee_external_id: str = "EMP-001",
    payroll_id: str | None = None,
    gross_pay: Decimal | str = "1000",
    allowances: Decimal | str = "200",
    deductions: Decimal | str = "100",
    net_pay: Decimal | str = "1100",
    allow_existing: bool = False,
) -> DemoPayrollPipelineResult:
    inputs = _demo_pipeline_inputs(
        company_id=company_id,
        employee_external_id=employee_external_id,
        payroll_id=payroll_id,
        gross_pay=gross_pay,
        allowances=allowances,
        deductions=deductions,
        net_pay=net_pay,
    )
    steps: list[PayrollCommandStepResult] = []

    steps.append(
        create_payroll_vault(
            **inputs["vault"],
            sync_after=True,
            allow_existing=allow_existing,
        )
    )
    vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=inputs["vault"]["payroll_id"])

    steps.append(
        add_salary_allocation(
            company_id=vault.company_id,
            payroll_id=vault.payroll_id,
            allocation_employee_wallet=inputs["enrollment"].employee_wallet_party,
            employee_external_id=inputs["employee_external_id"],
            salary_breakdown=inputs["salary_breakdown"],
            enrollment_cid=inputs["enrollment"].contract_id,
            sync_after=True,
            allow_existing=allow_existing,
        )
    )

    steps.append(
        finalize_allocations(
            company_id=vault.company_id,
            payroll_id=vault.payroll_id,
            sync_after=True,
            allow_existing=allow_existing,
        )
    )

    final_vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=vault.payroll_id)
    allocation = _active_salary_allocation(
        company_id=company_id,
        payroll_id=vault.payroll_id,
        employee_external_id=inputs["employee_external_id"],
    )
    return DemoPayrollPipelineResult(
        status="ok",
        company_id=company_id,
        payroll_id=vault.payroll_id,
        employee_external_id=inputs["employee_external_id"],
        steps=steps,
        payroll_vault_contract_id=final_vault.contract_id,
        salary_allocation_contract_id=allocation.contract_id if allocation is not None else "",
        payroll_vault_status=final_vault.vault_status,
    )


def preflight_demo_funding_activation_ticket_pipeline(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str = "EMP-001",
    funding_amount: Decimal | str | None = None,
    funding_reference: str | None = None,
) -> DemoFundingTicketPipelinePreflightResult:
    vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    allocation = _active_salary_allocation_or_error(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    resolved_amount = _resolved_funding_amount(vault, funding_amount)
    resolved_reference = _resolved_funding_reference(payroll_id, funding_reference)
    steps: list[dict[str, Any]] = []

    if vault.vault_status == FINALIZED_STATUS:
        steps.append(
            preflight_confirm_funding(
                company_id=company_id,
                payroll_id=payroll_id,
                funding_amount=resolved_amount,
                funding_reference=resolved_reference,
            ).safe_summary()
        )
        steps.append(
            {
                "status": "pending_funding",
                "template": PAYROLL_VAULT.display_id(),
                "choice": CHOICES["PayrollVault"]["ActivatePayroll"],
            }
        )
    elif vault.vault_status == FUNDED_STATUS:
        steps.append({"status": "exists", "choice": CHOICES["PayrollVault"]["ConfirmFunding"]})
        steps.append(preflight_activate_payroll(company_id=company_id, payroll_id=payroll_id).safe_summary())
    elif vault.vault_status == ACTIVE_STATUS:
        steps.append({"status": "exists", "choice": CHOICES["PayrollVault"]["ConfirmFunding"]})
        steps.append({"status": "exists", "choice": CHOICES["PayrollVault"]["ActivatePayroll"]})
    else:
        raise OnboardingValidationError("Payroll vault must be AllocationsFinalized, Funded, or Active for this pipeline.")

    if vault.vault_status == ACTIVE_STATUS:
        try:
            steps.append(
                preflight_issue_claim_ticket(
                    company_id=company_id,
                    payroll_id=payroll_id,
                    employee_external_id=employee_external_id,
                ).safe_summary()
            )
        except DuplicateClaimTicketError:
            ticket = _claim_ticket_for_employee(
                company_id=company_id,
                payroll_id=payroll_id,
                employee_external_id=employee_external_id,
            )
            steps.append(
                {
                    "status": "exists",
                    "choice": CHOICES["SalaryAllocation"]["IssueClaimTicket"],
                    "contract_id": ticket.contract_id if ticket is not None else "",
                }
            )
    else:
        steps.append(
            {
                "status": "pending_payroll_activation",
                "template": SALARY_ALLOCATION.display_id(),
                "choice": CHOICES["SalaryAllocation"]["IssueClaimTicket"],
                "salary_allocation_contract_id": allocation.contract_id,
            }
        )

    return DemoFundingTicketPipelinePreflightResult(
        status="ok",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=allocation.employee_external_id,
        steps=steps,
    )


def create_demo_funding_activation_ticket_pipeline(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str = "EMP-001",
    funding_amount: Decimal | str | None = None,
    funding_reference: str | None = None,
    allow_existing: bool = False,
) -> DemoFundingTicketPipelineResult:
    initial_vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    resolved_amount = _resolved_funding_amount(initial_vault, funding_amount)
    resolved_reference = _resolved_funding_reference(payroll_id, funding_reference)
    steps: list[PayrollCommandStepResult] = []

    steps.append(
        confirm_funding(
            company_id=company_id,
            payroll_id=payroll_id,
            funding_amount=resolved_amount,
            funding_reference=resolved_reference,
            funding_proof=None,
            sync_after=True,
            allow_existing=allow_existing,
        )
    )
    funded_or_active_vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)

    steps.append(
        activate_payroll(
            company_id=funded_or_active_vault.company_id,
            payroll_id=funded_or_active_vault.payroll_id,
            sync_after=True,
            allow_existing=allow_existing,
        )
    )
    active_vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)

    steps.append(
        issue_claim_ticket(
            company_id=active_vault.company_id,
            payroll_id=active_vault.payroll_id,
            employee_external_id=employee_external_id,
            sync_after=True,
            allow_existing=allow_existing,
        )
    )

    final_vault = _active_payroll_vault_or_error(company_id=company_id, payroll_id=payroll_id)
    allocation = _active_salary_allocation(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    ticket = _claim_ticket_for_employee(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    pipeline_status = PENDING_CLAIM_WINDOW_OPEN_STATUS if steps[-1].status == PENDING_CLAIM_WINDOW_OPEN_STATUS else "ok"
    return DemoFundingTicketPipelineResult(
        status=pipeline_status,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        steps=steps,
        payroll_vault_contract_id=final_vault.contract_id,
        salary_allocation_contract_id=allocation.contract_id if allocation is not None else "",
        claim_ticket_contract_id=ticket.contract_id if ticket is not None else "",
        payroll_vault_status=final_vault.vault_status,
        salary_allocation_status=allocation.allocation_status if allocation is not None else "",
    )


def _submit_choice(
    *,
    action_slug: str,
    workflow_id: str,
    act_as: list[str],
    template: TemplateRef,
    contract_id: str,
    choice: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    read_as = default_read_parties()
    command_id = _new_command_id(action_slug)
    ledger_command = LedgerCommand.objects.create(
        command_id=command_id,
        workflow_id=workflow_id,
        act_as=act_as,
        read_as=read_as,
        template_id=template.display_id(),
        contract_id=contract_id,
        choice_name=choice,
        payload=payload,
        status=CommandStatus.PENDING,
    )

    client = LedgerClient(load_ledger_auth_settings())
    try:
        ledger_command.status = CommandStatus.SUBMITTED
        ledger_command.submitted_at = timezone.now()
        ledger_command.save(update_fields=["status", "submitted_at", "updated_at"])
        result = client.submit_exercise(
            context=CommandContext(
                act_as=act_as,
                read_as=read_as,
                command_id=command_id,
                workflow_id=workflow_id,
            ),
            template=template,
            contract_id=contract_id,
            choice=choice,
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
    return {"command": ledger_command, "result": result}


def _demo_pipeline_inputs(
    *,
    company_id: str,
    employee_external_id: str,
    payroll_id: str | None,
    gross_pay: Decimal | str,
    allowances: Decimal | str,
    deductions: Decimal | str,
    net_pay: Decimal | str,
) -> dict[str, Any]:
    company = _company_or_error(company_id)
    enrollment = _enrollment_or_error(
        company_id=company.company_id,
        employee_external_id=employee_external_id,
        enrollment_cid=None,
    )
    payroll_token = _first_token(company)
    now = timezone.now()
    period_start = now.date()
    period_end = period_start + timedelta(days=30)
    resolved_payroll_id = (payroll_id or "zalary-payroll-demo-001").strip()
    salary_breakdown = salary_breakdown_payload(
        gross_pay=gross_pay,
        allowances=allowances,
        deductions=deductions,
        net_pay=net_pay,
        token=payroll_token,
    )
    _validate_salary_breakdown(salary_breakdown)
    return {
        "company": company,
        "enrollment": enrollment,
        "employee_external_id": enrollment.employee_external_id,
        "salary_breakdown": salary_breakdown,
        "vault": {
            "company_id": company.company_id,
            "hr_wallet": _first_wallet(company.hr_wallet_parties, "hrWallet"),
            "employer_wallet": _first_wallet(company.employer_wallet_parties, "employerWallet"),
            "payroll_id": resolved_payroll_id,
            "payroll_period": {
                "label": resolved_payroll_id,
                "startsAt": period_start,
                "endsAt": period_end,
            },
            "payroll_token": payroll_token,
            "claim_window_start": now,
            "claim_window_end": now + timedelta(days=30),
            "expected_employee_count": 1,
        },
    }


def _validate_demo_allocation_inputs(inputs: dict[str, Any]) -> None:
    company = inputs["company"]
    enrollment = inputs["enrollment"]
    if enrollment.hr_wallet_party not in (company.hr_wallet_parties or []):
        raise OnboardingValidationError("Enrollment HR wallet is not approved for this company.")
    if enrollment.employer_wallet_party not in (company.employer_wallet_parties or []):
        raise OnboardingValidationError("Enrollment employer wallet is not approved for this company.")
    if not _same_token(inputs["salary_breakdown"]["token"], inputs["vault"]["payroll_token"]):
        raise OnboardingValidationError("salaryBreakdown token must match payrollToken.")


def _payroll_vault_payload_or_error(**kwargs: Any) -> dict[str, Any]:
    try:
        return create_payroll_vault_choice_payload(**kwargs)
    except ValueError as exc:
        raise OnboardingValidationError(str(exc)) from exc


def _salary_allocation_payload_or_error(**kwargs: Any) -> dict[str, Any]:
    try:
        return add_salary_allocation_choice_payload(**kwargs)
    except ValueError as exc:
        raise OnboardingValidationError(str(exc)) from exc


def _confirm_funding_payload_or_error(**kwargs: Any) -> dict[str, Any]:
    try:
        return confirm_funding_choice_payload(**kwargs)
    except ValueError as exc:
        raise OnboardingValidationError(str(exc)) from exc


def _company_or_error(company_id: str) -> CompanyMirror:
    company = CompanyMirror.objects.filter(company_id=(company_id or "").strip()).order_by("-synced_at").first()
    if company is None:
        raise OnboardingValidationError(f"Company not found in local mirror: {company_id}.")
    return company


def _enrollment_or_error(
    *,
    company_id: str,
    employee_external_id: str,
    enrollment_cid: str | None,
) -> EmployeeEnrollmentMirror:
    queryset = EmployeeEnrollmentMirror.objects.filter(company_id=company_id, is_active=True)
    if enrollment_cid:
        queryset = queryset.filter(contract_id=enrollment_cid)
    else:
        queryset = queryset.filter(employee_external_id=(employee_external_id or "").strip())
    enrollment = queryset.order_by("-synced_at").first()
    if enrollment is None:
        raise OnboardingValidationError("Active employee enrollment not found in local mirror.")
    return enrollment


def _active_payroll_vault_or_error(*, company_id: str, payroll_id: str) -> PayrollVaultMirror:
    vault = _active_payroll_vault(company_id=company_id, payroll_id=payroll_id)
    if vault is None:
        raise OnboardingValidationError(f"Active payroll vault not found locally for {company_id}/{payroll_id}.")
    return vault


def _active_payroll_vault(*, company_id: str, payroll_id: str) -> PayrollVaultMirror | None:
    return (
        PayrollVaultMirror.objects.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
        )
        .exclude(vault_status=ARCHIVED_STATUS)
        .order_by("-synced_at")
        .first()
    )


def _active_salary_allocation(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> SalaryAllocationMirror | None:
    return (
        SalaryAllocationMirror.objects.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
            employee_external_id=(employee_external_id or "").strip(),
        )
        .exclude(allocation_status=ARCHIVED_STATUS)
        .order_by("-synced_at")
        .first()
    )


def _active_salary_allocation_or_error(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> SalaryAllocationMirror:
    allocation = _active_salary_allocation(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )
    if allocation is None:
        raise OnboardingValidationError(
            f"Active salary allocation not found locally for {company_id}/{payroll_id}/{employee_external_id}."
        )
    return allocation


def _claim_ticket_for_employee(
    *,
    company_id: str,
    payroll_id: str,
    employee_external_id: str,
) -> ClaimTicketMirror | None:
    return (
        ClaimTicketMirror.objects.filter(
            company_id=(company_id or "").strip(),
            payroll_id=(payroll_id or "").strip(),
            employee_external_id=(employee_external_id or "").strip(),
        )
        .order_by("-synced_at")
        .first()
    )


def _validate_period(period: dict[str, Any]) -> None:
    starts_at = parse_date(str(period.get("startsAt") or ""))
    ends_at = parse_date(str(period.get("endsAt") or ""))
    if starts_at is None or ends_at is None:
        raise OnboardingValidationError("payrollPeriod dates must be valid ISO dates.")
    if starts_at > ends_at:
        raise OnboardingValidationError("payrollPeriod.startsAt must be before or equal to payrollPeriod.endsAt.")


def _validate_claim_window(start: str, end: str) -> None:
    starts_at = _parse_required_datetime(start, "claimWindowStart")
    ends_at = _parse_required_datetime(end, "claimWindowEnd")
    if starts_at >= ends_at:
        raise OnboardingValidationError("claimWindowStart must be before claimWindowEnd.")


def _parse_required_datetime(value: str, field_name: str):
    parsed = parse_datetime(value)
    if parsed is None:
        raise OnboardingValidationError(f"{field_name} must be a valid ISO timestamp.")
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.utc)
    return parsed


def _validate_salary_breakdown(salary: dict[str, Any]) -> None:
    gross_pay = _decimal(salary.get("grossPay"))
    allowances = _decimal(salary.get("allowances"))
    deductions = _decimal(salary.get("deductions"))
    net_pay = _decimal(salary.get("netPay"))
    if gross_pay < 0 or allowances < 0 or deductions < 0:
        raise OnboardingValidationError("Salary amounts cannot be negative.")
    if net_pay <= 0:
        raise OnboardingValidationError("netPay must be greater than zero.")
    if net_pay != gross_pay + allowances - deductions:
        raise OnboardingValidationError("netPay must equal grossPay plus allowances minus deductions.")


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError) as exc:
        raise OnboardingValidationError("Decimal value is invalid.") from exc


def _total_net_pay(vault: PayrollVaultMirror) -> Decimal:
    return _decimal(vault.totals.get("totalNetPay") or vault.payload.get("totalNetPay"))


def _resolved_funding_amount(vault: PayrollVaultMirror, funding_amount: Decimal | str | None) -> str:
    value = funding_amount if funding_amount is not None and str(funding_amount).strip() else _total_net_pay(vault)
    try:
        return decimal_to_daml(value)
    except ValueError as exc:
        raise OnboardingValidationError(str(exc)) from exc


def _resolved_funding_reference(payroll_id: str, funding_reference: str | None) -> str:
    reference = (funding_reference or f"FUND-{payroll_id}").strip()
    if not reference:
        raise OnboardingValidationError("fundingReference is required.")
    return reference


def _uploaded_count(vault: PayrollVaultMirror) -> int:
    return int(vault.totals.get("uploadedAllocationCount") or vault.payload.get("uploadedAllocationCount") or 0)


def _expected_count(vault: PayrollVaultMirror) -> int:
    return int(vault.totals.get("expectedEmployeeCount") or vault.payload.get("expectedEmployeeCount") or 0)


def _token_in_list(token: dict[str, Any], allowed_tokens: list[dict[str, Any]]) -> bool:
    return any(_same_token(token, allowed_token) for allowed_token in allowed_tokens)


def _same_token(left: dict[str, Any], right: dict[str, Any]) -> bool:
    fields = ("symbol", "instrumentId", "instrumentAdmin", "utilityApiUrl", "xReserveApiUrl")
    return all(str(left.get(field) or "") == str(right.get(field) or "") for field in fields)


def _validate_allocation_matches_vault(allocation: SalaryAllocationMirror, vault: PayrollVaultMirror) -> None:
    if allocation.hr_wallet_party != vault.hr_wallet_party:
        raise OnboardingValidationError("SalaryAllocation HR wallet does not match the payroll vault.")
    if allocation.employer_wallet_party != vault.employer_wallet_party:
        raise OnboardingValidationError("SalaryAllocation employer wallet does not match the payroll vault.")
    if allocation.company_id != vault.company_id:
        raise OnboardingValidationError("SalaryAllocation company ID does not match the payroll vault.")
    if allocation.company_admin_party != vault.company_admin_party:
        raise OnboardingValidationError("SalaryAllocation company admin does not match the payroll vault.")
    if allocation.payroll_id != vault.payroll_id:
        raise OnboardingValidationError("SalaryAllocation payroll ID does not match the payroll vault.")
    if not _same_token((allocation.salary_breakdown or {}).get("token") or {}, vault.payroll_token or {}):
        raise OnboardingValidationError("SalaryAllocation token does not match the payroll vault.")
    allocated_employees = vault.totals.get("allocatedEmployees") or vault.payload.get("allocatedEmployees") or []
    allocated_external_ids = (
        vault.totals.get("allocatedEmployeeExternalIds")
        or vault.payload.get("allocatedEmployeeExternalIds")
        or []
    )
    if allocation.employee_wallet_party not in allocated_employees:
        raise OnboardingValidationError("SalaryAllocation employee wallet is not in the payroll vault.")
    if allocation.employee_external_id not in allocated_external_ids:
        raise OnboardingValidationError("SalaryAllocation employee external ID is not in the payroll vault.")


def _aware_datetime_or_error(value: Any, field_name: str):
    if value is None:
        raise OnboardingValidationError(f"{field_name} is missing from the payroll vault mirror.")
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            raise OnboardingValidationError(f"{field_name} must be a valid ISO timestamp.")
        value = parsed
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.utc)
    return value


def _first_token(company: CompanyMirror) -> dict[str, Any]:
    for token in company.allowed_tokens or []:
        if token:
            return token
    raise OnboardingValidationError("Company has no allowed payroll token in the local mirror.")


def _first_wallet(values: list[str], field_name: str) -> str:
    for value in values or []:
        cleaned = (value or "").strip()
        if cleaned:
            return cleaned
    raise OnboardingValidationError(f"{field_name} is required but no mirrored company wallet is available.")


def _new_command_id(action: str) -> str:
    prefix = (os.environ.get(COMMAND_ID_PREFIX) or "zalary").strip()
    return f"{prefix}-{action}-{uuid4().hex}"


def _existing_step(
    *,
    action: str,
    company_id: str,
    payroll_id: str,
    choice_name: str,
    contract_id: str,
    existing_contract: dict[str, Any],
    reason: str = "",
    salary_allocation_contract_id: str = "",
) -> PayrollCommandStepResult:
    return PayrollCommandStepResult(
        status="exists",
        action=action,
        command_id="",
        update_id=None,
        company_id=company_id,
        payroll_id=payroll_id,
        ledger_command_pk=0,
        contract_id=contract_id,
        choice_name=choice_name,
        existing_contract=existing_contract,
        reason=reason,
        salary_allocation_contract_id=salary_allocation_contract_id,
    )


def _payroll_vault_summary(vault: PayrollVaultMirror) -> dict[str, Any]:
    return {
        "contract_id": vault.contract_id,
        "company_id": vault.company_id,
        "payroll_id": vault.payroll_id,
        "hr_wallet_party": vault.hr_wallet_party,
        "employer_wallet_party": vault.employer_wallet_party,
        "vault_status": vault.vault_status,
        "expected_employee_count": _expected_count(vault),
        "uploaded_allocation_count": _uploaded_count(vault),
    }


def _salary_allocation_summary(allocation: SalaryAllocationMirror) -> dict[str, Any]:
    return {
        "contract_id": allocation.contract_id,
        "company_id": allocation.company_id,
        "payroll_id": allocation.payroll_id,
        "employee_external_id": allocation.employee_external_id,
        "employee_wallet_party": allocation.employee_wallet_party,
        "allocation_status": allocation.allocation_status,
    }


def _claim_ticket_summary(ticket: ClaimTicketMirror) -> dict[str, Any]:
    return {
        "contract_id": ticket.contract_id,
        "company_id": ticket.company_id,
        "payroll_id": ticket.payroll_id,
        "employee_external_id": ticket.employee_external_id,
        "employee_wallet_party": ticket.employee_wallet_party,
        "source_allocation_contract_id": ticket.source_allocation_contract_id,
        "ticket_amount": str(ticket.ticket_amount),
    }
