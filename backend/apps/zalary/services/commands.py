from dataclasses import dataclass, field
import os
from typing import Any
from uuid import uuid4

from django.utils import timezone

from apps.zalary.models import CommandStatus, CompanyMirror, LedgerCommand

from .auth import (
    COMMAND_ID_PREFIX,
    PLATFORM_ADMIN_PARTY,
    PLATFORM_CONFIG_CONTRACT_ID,
    default_read_parties,
    load_ledger_auth_settings,
)
from .errors import (
    ConfigurationError,
    DuplicateCompanyError,
    LedgerNotImplementedError,
    LedgerSubmissionError,
    OnboardingValidationError,
    safe_error_message,
)
from .ledger import CommandContext, LedgerClient
from .payloads import create_company_choice_payload
from .sync import CompanySyncResult, sync_companies
from .templates import CHOICES, ZALARY_CONFIG


@dataclass(frozen=True)
class CreateCompanyResult:
    status: str
    command_id: str
    update_id: str | None
    company_id: str
    company_name: str
    act_as: list[str]
    read_as: list[str]
    ledger_command_pk: int
    synced_companies: CompanySyncResult | None = None
    sync_error: str = ""
    existing_company: dict[str, Any] | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "status": self.status,
            "command_id": self.command_id,
            "update_id": self.update_id,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "act_as": self.act_as,
            "read_as": self.read_as,
            "ledger_command_id": self.ledger_command_pk,
        }
        if self.existing_company is not None:
            summary["existing_company"] = self.existing_company
        if self.synced_companies is not None:
            summary["synced_companies"] = self.synced_companies.safe_summary()
        if self.sync_error:
            summary["sync_error"] = self.sync_error
        return summary


def create_company(
    *,
    company_name: str,
    company_id: str,
    platform_config_contract_id: str | None = None,
    company_admin: str | None = None,
    admin_wallets: list[str] | None = None,
    hr_wallets: list[str] | None = None,
    employer_wallets: list[str] | None = None,
    allowed_tokens: list[dict[str, Any]] | None = None,
    sync_after: bool = True,
    allow_single_party_demo: bool = True,
    allow_existing: bool = False,
) -> CreateCompanyResult:
    from apps.zalary.selectors import active_platform_config

    config = active_platform_config()
    if config is None or not config.ledger_active or not config.is_active:
        raise ConfigurationError("An active mirrored ZalaryConfig is required before creating a company.")

    resolved_contract_id = (
        _clean_optional(platform_config_contract_id)
        or _clean_optional(os.environ.get(PLATFORM_CONFIG_CONTRACT_ID))
        or config.contract_id
    )
    if not resolved_contract_id:
        raise ConfigurationError("A platform config contract id is required before creating a company.")

    platform_admin = (
        _clean_optional(os.environ.get(PLATFORM_ADMIN_PARTY))
        or _clean_optional(config.platform_admin_party)
    )
    if not platform_admin:
        raise ConfigurationError("A platform admin party is required before creating a company.")

    resolved_company_admin = _clean_optional(company_admin)
    if not resolved_company_admin:
        if not allow_single_party_demo:
            raise OnboardingValidationError(
                "companyAdmin is required unless ZALARY_ALLOW_SINGLE_PARTY_DEMO=true."
            )
        resolved_company_admin = platform_admin
    resolved_admin_wallets = admin_wallets or [resolved_company_admin]
    resolved_hr_wallets = hr_wallets or [resolved_company_admin]
    resolved_employer_wallets = employer_wallets or [resolved_company_admin]
    resolved_allowed_tokens = allowed_tokens or [config.default_token]

    try:
        payload = create_company_choice_payload(
            companyAdmin=resolved_company_admin,
            companyName=company_name,
            companyId=company_id,
            adminWallets=resolved_admin_wallets,
            hrWallets=resolved_hr_wallets,
            employerWallets=resolved_employer_wallets,
            allowedTokens=resolved_allowed_tokens,
        )
    except ValueError as exc:
        raise OnboardingValidationError(str(exc)) from exc

    existing_company = _existing_company_for_platform_admin(
        platform_admin=platform_admin,
        company_id=payload["companyId"],
    )
    if existing_company is not None:
        if allow_existing:
            return CreateCompanyResult(
                status="exists",
                command_id="",
                update_id=None,
                company_id=existing_company.company_id,
                company_name=existing_company.company_name,
                act_as=[],
                read_as=default_read_parties(),
                ledger_command_pk=0,
                existing_company=_company_summary(existing_company),
            )
        raise DuplicateCompanyError(
            f"Company already exists locally for this platform admin: {payload['companyId']}."
        )

    act_as = _dedupe_parties([platform_admin, resolved_company_admin])
    read_as = default_read_parties()
    command_id = _new_command_id("create-company")
    workflow_id = f"zalary-create-company-{payload['companyId']}"

    ledger_command = LedgerCommand.objects.create(
        command_id=command_id,
        workflow_id=workflow_id,
        act_as=act_as,
        read_as=read_as,
        template_id=ZALARY_CONFIG.display_id(),
        contract_id=resolved_contract_id,
        choice_name=CHOICES["ZalaryConfig"]["CreateCompany"],
        payload=payload,
        status=CommandStatus.PENDING,
    )

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    now = timezone.now()
    try:
        ledger_command.status = CommandStatus.SUBMITTED
        ledger_command.submitted_at = now
        ledger_command.save(update_fields=["status", "submitted_at", "updated_at"])

        result = client.submit_exercise(
            context=CommandContext(
                act_as=act_as,
                read_as=read_as,
                command_id=command_id,
                workflow_id=workflow_id,
            ),
            template=ZALARY_CONFIG,
            contract_id=resolved_contract_id,
            choice=CHOICES["ZalaryConfig"]["CreateCompany"],
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

    synced_companies = None
    sync_error = ""
    if sync_after:
        try:
            synced_companies = sync_companies(parties=read_as)
        except Exception as exc:
            sync_error = safe_error_message(exc)

    return CreateCompanyResult(
        status="ok",
        command_id=command_id,
        update_id=result.update_id,
        company_id=payload["companyId"],
        company_name=payload["companyName"],
        act_as=act_as,
        read_as=read_as,
        ledger_command_pk=ledger_command.pk,
        synced_companies=synced_companies,
        sync_error=sync_error,
        raw_response=result.raw_response,
    )


def _new_command_id(action: str) -> str:
    prefix = _clean_optional(os.environ.get(COMMAND_ID_PREFIX)) or "zalary"
    return f"{prefix}-{action}-{uuid4().hex}"


def _clean_optional(value: str | None) -> str:
    if not value:
        return ""
    return value.strip()


def _dedupe_parties(parties: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for party in parties:
        cleaned = _clean_optional(party)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _existing_company_for_platform_admin(*, platform_admin: str, company_id: str) -> CompanyMirror | None:
    return (
        CompanyMirror.objects.filter(
            platform_admin_party=platform_admin,
            company_id=company_id,
        )
        .order_by("-synced_at")
        .first()
    )


def _company_summary(company: CompanyMirror) -> dict[str, Any]:
    return {
        "contract_id": company.contract_id,
        "company_id": company.company_id,
        "company_name": company.company_name,
        "platform_admin_party": company.platform_admin_party,
        "company_admin_party": company.company_admin_party,
        "admin_wallet_parties": company.admin_wallet_parties,
        "hr_wallet_parties": company.hr_wallet_parties,
        "employer_wallet_parties": company.employer_wallet_parties,
        "allowed_tokens": company.allowed_tokens,
    }


def sync_or_create_company(*, platform_config_contract_id: str, payload: dict[str, Any]) -> CreateCompanyResult:
    return create_company(
        platform_config_contract_id=platform_config_contract_id,
        company_name=payload["companyName"],
        company_id=payload["companyId"],
        company_admin=payload.get("companyAdmin"),
        admin_wallets=payload.get("adminWallets"),
        hr_wallets=payload.get("hrWallets"),
        employer_wallets=payload.get("employerWallets"),
        allowed_tokens=payload.get("allowedTokens"),
    )


def update_company_allowed_tokens(*, platform_config_contract_id: str, payload: dict[str, Any]) -> None:
    # TODO: Exercise ZalaryConfig.UpdateCompanyAllowedTokens.
    raise LedgerNotImplementedError("Company allowed-token update workflow is not implemented yet.")


def create_employee_enrollment(*, company_id: str, payload: dict[str, Any]):
    from .enrollment import create_employee_enrollment as create_employee_enrollment_command

    return create_employee_enrollment_command(
        company_id=company_id,
        hr_wallet=payload["hrWallet"],
        employer_wallet=payload["employerWallet"],
        employee_wallet=payload["employeeWallet"],
        employee_external_id=payload["employeeExternalId"],
    )


def create_payroll_vault(*, company_id: str, payload: dict[str, Any]):
    from .payroll import create_payroll_vault as create_payroll_vault_command

    return create_payroll_vault_command(
        company_id=company_id,
        hr_wallet=payload["hrWallet"],
        employer_wallet=payload["employerWallet"],
        payroll_id=payload["payrollId"],
        payroll_period=payload["payrollPeriod"],
        payroll_token=payload["payrollToken"],
        claim_window_start=payload["claimWindowStart"],
        claim_window_end=payload["claimWindowEnd"],
        expected_employee_count=payload["expectedEmployeeCount"],
    )


def add_salary_allocation(*, company_id: str, payroll_id: str, payload: dict[str, Any]):
    from .payroll import add_salary_allocation as add_salary_allocation_command

    return add_salary_allocation_command(
        company_id=company_id,
        payroll_id=payroll_id,
        allocation_employee_wallet=payload["allocationEmployeeWallet"],
        employee_external_id=payload["employeeExternalId"],
        salary_breakdown=payload["salaryBreakdown"],
        enrollment_cid=payload["enrollmentCid"],
    )


def finalize_allocations(*, company_id: str, payroll_id: str):
    from .payroll import finalize_allocations as finalize_allocations_command

    return finalize_allocations_command(company_id=company_id, payroll_id=payroll_id)


def confirm_funding(*, company_id: str, payroll_id: str, payload: dict[str, Any]):
    from .payroll import confirm_funding as confirm_funding_command

    return confirm_funding_command(
        company_id=company_id,
        payroll_id=payroll_id,
        funding_amount=payload["fundingAmount"],
        funding_reference=payload["fundingReference"],
        funding_proof=payload.get("fundingProof"),
    )


def activate_payroll(*, company_id: str, payroll_id: str):
    from .payroll import activate_payroll as activate_payroll_command

    return activate_payroll_command(company_id=company_id, payroll_id=payroll_id)


def issue_claim_ticket(*, company_id: str, payroll_id: str, employee_external_id: str):
    from .payroll import issue_claim_ticket as issue_claim_ticket_command

    return issue_claim_ticket_command(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )


def request_salary_claim(*, claim_ticket_contract_id: str | None = None, payload: dict[str, Any] | None = None):
    from .settlement import request_salary_claim as request_salary_claim_command

    data = payload or {}
    return request_salary_claim_command(
        claim_ticket_contract_id=claim_ticket_contract_id or data.get("claim_ticket_contract_id"),
        company_id=data.get("company_id") or data.get("companyId"),
        payroll_id=data.get("payroll_id") or data.get("payrollId"),
        employee_external_id=data.get("employee_external_id") or data.get("employeeExternalId"),
        allow_existing=bool(data.get("allow_existing") or data.get("allowExisting")),
    )


def confirm_salary_settlement(*, salary_claim_contract_id: str | None = None, payload: dict[str, Any]):
    from .settlement import confirm_salary_settlement as confirm_salary_settlement_command

    return confirm_salary_settlement_command(
        salary_claim_contract_id=salary_claim_contract_id or payload.get("salary_claim_contract_id"),
        company_id=payload.get("company_id") or payload.get("companyId"),
        payroll_id=payload.get("payroll_id") or payload.get("payrollId"),
        employee_external_id=payload.get("employee_external_id") or payload.get("employeeExternalId"),
        settlement_reference=payload["settlementReference"],
        settlement_proof=payload.get("settlementProof"),
        demo_proof=bool(payload.get("demoProof") or payload.get("demo_proof")),
        allow_existing=bool(payload.get("allowExisting") or payload.get("allow_existing")),
    )


def reject_salary_claim(*, salary_claim_contract_id: str, payload: dict[str, Any]) -> None:
    # TODO: Exercise SalaryClaim.RejectSalaryClaim.
    raise LedgerNotImplementedError("Salary claim rejection is not implemented yet.")
