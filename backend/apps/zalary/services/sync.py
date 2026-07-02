from dataclasses import dataclass
import os
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.zalary.models import (
    ClaimTicketMirror,
    CompanyMirror,
    EmployeeEnrollmentMirror,
    FailedSalaryClaimMirror,
    LedgerContract,
    PayslipMirror,
    PayrollVaultMirror,
    SalaryAllocationMirror,
    SalaryClaimMirror,
    SettledSalaryRecordMirror,
    SettlementReceiptMirror,
    ZalaryConfigMirror,
)

from .auth import DAML_PACKAGE_NAME, default_read_parties, load_ledger_auth_settings
from .errors import LedgerNotImplementedError, LedgerSyncError
from .ledger import LedgerClient
from .templates import (
    CLAIM_TICKET,
    COMPANY,
    DEFAULT_PACKAGE_NAME,
    EMPLOYEE_ENROLLMENT,
    FAILED_SALARY_CLAIM,
    FUNDING_RECEIPT,
    PAYSLIP,
    PAYROLL_VAULT,
    SALARY_ALLOCATION,
    SALARY_CLAIM,
    SETTLED_SALARY_RECORD,
    SETTLEMENT_RECEIPT,
    ZALARY_CONFIG,
)


@dataclass(frozen=True)
class ZalaryConfigSyncResult:
    synced_count: int
    contract_ids: list[str]
    marked_inactive_count: int

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "contract_ids": self.contract_ids,
            "marked_inactive_count": self.marked_inactive_count,
        }


@dataclass(frozen=True)
class CompanySyncResult:
    synced_count: int
    company_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "contract_ids": self.contract_ids,
        }


@dataclass(frozen=True)
class EmployeeEnrollmentSyncResult:
    synced_count: int
    company_ids: list[str]
    employee_external_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "employee_external_ids": self.employee_external_ids,
            "contract_ids": self.contract_ids,
        }


@dataclass(frozen=True)
class PayrollVaultSyncResult:
    synced_count: int
    company_ids: list[str]
    payroll_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "payroll_ids": self.payroll_ids,
            "contract_ids": self.contract_ids,
        }


@dataclass(frozen=True)
class SalaryAllocationSyncResult:
    synced_count: int
    company_ids: list[str]
    payroll_ids: list[str]
    employee_external_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "payroll_ids": self.payroll_ids,
            "employee_external_ids": self.employee_external_ids,
            "contract_ids": self.contract_ids,
        }


@dataclass(frozen=True)
class FundingReceiptSyncResult:
    synced_count: int
    company_ids: list[str]
    payroll_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "payroll_ids": self.payroll_ids,
            "contract_ids": self.contract_ids,
        }


@dataclass(frozen=True)
class ClaimTicketSyncResult:
    synced_count: int
    company_ids: list[str]
    payroll_ids: list[str]
    employee_external_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "payroll_ids": self.payroll_ids,
            "employee_external_ids": self.employee_external_ids,
            "contract_ids": self.contract_ids,
        }


@dataclass(frozen=True)
class SalaryClaimSyncResult:
    synced_count: int
    company_ids: list[str]
    payroll_ids: list[str]
    employee_external_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "payroll_ids": self.payroll_ids,
            "employee_external_ids": self.employee_external_ids,
            "contract_ids": self.contract_ids,
        }


@dataclass(frozen=True)
class AuditContractSyncResult:
    record_type: str
    synced_count: int
    company_ids: list[str]
    payroll_ids: list[str]
    employee_external_ids: list[str]
    contract_ids: list[str]

    def safe_summary(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "synced_count": self.synced_count,
            "company_ids": self.company_ids,
            "payroll_ids": self.payroll_ids,
            "employee_external_ids": self.employee_external_ids,
            "contract_ids": self.contract_ids,
        }


def sync_zalary_config(*, parties: list[str] | None = None) -> ZalaryConfigSyncResult:
    settings = load_ledger_auth_settings()
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync ZalaryConfig.")

    client = LedgerClient(settings)
    contracts = client.query_zalary_config_contracts(parties=read_parties)
    seen_contract_ids: list[str] = []

    for contract in contracts:
        mirror = upsert_zalary_config_contract(contract)
        seen_contract_ids.append(mirror.contract_id)

    stale_queryset = ZalaryConfigMirror.objects.filter(ledger_active=True)
    if seen_contract_ids:
        stale_queryset = stale_queryset.exclude(contract_id__in=seen_contract_ids)
    marked_inactive_count = stale_queryset.update(ledger_active=False, last_seen_at=timezone.now())

    return ZalaryConfigSyncResult(
        synced_count=len(seen_contract_ids),
        contract_ids=seen_contract_ids,
        marked_inactive_count=marked_inactive_count,
    )


def upsert_zalary_config_contract(contract: dict[str, Any]) -> ZalaryConfigMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    package_name = contract.get("package_name") or os.environ.get(DAML_PACKAGE_NAME) or DEFAULT_PACKAGE_NAME
    template_display_id = _template_display_id(template_info)
    ledger_created_at = _parse_optional_datetime(contract.get("created_at") or payload.get("createdAt"))
    ledger_offset = str(contract.get("ledger_offset") or "")
    now = timezone.now()

    mirror, _created = ZalaryConfigMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "package_name": package_name,
            "template_id": template_display_id,
            "platform_admin_party": str(payload.get("platformAdmin") or ""),
            "supported_tokens": payload.get("supportedTokens") or [],
            "default_token": payload.get("defaultToken") or {},
            "is_active": bool(payload.get("isActive", False)),
            "ledger_active": True,
            "ledger_created_at": ledger_created_at,
            "last_seen_at": now,
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )

    LedgerContract.objects.update_or_create(
        contract_id=mirror.contract_id,
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or ZALARY_CONFIG.module_name,
            "entity_name": template_info.get("entity_name") or ZALARY_CONFIG.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return mirror


def _template_display_id(template_info: dict[str, str]) -> str:
    return _template_display_id_for(template_info, ZALARY_CONFIG.module_name, ZALARY_CONFIG.entity_name)


def _template_display_id_for(template_info: dict[str, str], module_name_default: str, entity_name_default: str) -> str:
    package_id = template_info.get("package_id") or ""
    module_name = template_info.get("module_name") or module_name_default
    entity_name = template_info.get("entity_name") or entity_name_default
    if package_id:
        return f"{package_id}:{module_name}:{entity_name}"
    return f"{module_name}:{entity_name}"


def _parse_optional_datetime(value: Any):
    if not isinstance(value, str) or not value:
        return None

    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.utc)
    return parsed


def sync_active_contracts(*, parties: list[str], template_names: list[str] | None = None) -> None:
    # TODO: Query active contracts and upsert LedgerContract plus template-specific mirrors.
    raise LedgerNotImplementedError("Active contract sync is not implemented yet.")


def sync_companies(*, parties: list[str] | None = None) -> CompanySyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync Company contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=COMPANY,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )

    company_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        mirror = upsert_company_contract(contract)
        company_ids.append(mirror.company_id)
        contract_ids.append(mirror.contract_id)

    return CompanySyncResult(
        synced_count=len(contract_ids),
        company_ids=company_ids,
        contract_ids=contract_ids,
    )


def upsert_company_contract(contract: dict[str, Any]) -> CompanyMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(template_info, COMPANY.module_name, COMPANY.entity_name)
    ledger_offset = str(contract.get("ledger_offset") or "")

    mirror, _created = CompanyMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("companyId") or ""),
            "company_name": str(payload.get("companyName") or ""),
            "platform_admin_party": str(payload.get("platformAdmin") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "admin_wallet_parties": payload.get("adminWallets") or [],
            "hr_wallet_parties": payload.get("hrWallets") or [],
            "employer_wallet_parties": payload.get("employerWallets") or [],
            "allowed_tokens": payload.get("allowedTokens") or [],
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )

    LedgerContract.objects.update_or_create(
        contract_id=mirror.contract_id,
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or COMPANY.module_name,
            "entity_name": template_info.get("entity_name") or COMPANY.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return mirror


def sync_employee_enrollments(
    *,
    company_id: str | None = None,
    parties: list[str] | None = None,
) -> EmployeeEnrollmentSyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync EmployeeEnrollment contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=EMPLOYEE_ENROLLMENT,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )
    if company_id:
        contracts = [
            contract
            for contract in contracts
            if (contract.get("payload") or {}).get("companyId") == company_id
        ]

    company_ids: list[str] = []
    employee_external_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        mirror = upsert_employee_enrollment_contract(contract)
        company_ids.append(mirror.company_id)
        employee_external_ids.append(mirror.employee_external_id)
        contract_ids.append(mirror.contract_id)

    return EmployeeEnrollmentSyncResult(
        synced_count=len(contract_ids),
        company_ids=company_ids,
        employee_external_ids=employee_external_ids,
        contract_ids=contract_ids,
    )


def upsert_employee_enrollment_contract(contract: dict[str, Any]) -> EmployeeEnrollmentMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        EMPLOYEE_ENROLLMENT.module_name,
        EMPLOYEE_ENROLLMENT.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")

    mirror, _created = EmployeeEnrollmentMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("companyId") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "hr_wallet_party": str(payload.get("hrWallet") or ""),
            "employer_wallet_party": str(payload.get("employerWallet") or ""),
            "employee_wallet_party": str(payload.get("employeeWallet") or ""),
            "employee_external_id": str(payload.get("employeeExternalId") or ""),
            "is_active": bool(payload.get("isActive", False)),
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )

    LedgerContract.objects.update_or_create(
        contract_id=mirror.contract_id,
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or EMPLOYEE_ENROLLMENT.module_name,
            "entity_name": template_info.get("entity_name") or EMPLOYEE_ENROLLMENT.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return mirror


def sync_payroll_state(*, company_id: str, payroll_id: str, parties: list[str]) -> None:
    # TODO: Sync payroll vault, allocations, claim tickets, claims, and audit records.
    raise LedgerNotImplementedError("Payroll state sync is not implemented yet.")


def sync_payroll_vaults(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    parties: list[str] | None = None,
) -> PayrollVaultSyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync PayrollVault contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=PAYROLL_VAULT,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )
    contracts = _filter_contract_payloads(contracts, company_id=company_id, payroll_id=payroll_id)

    company_ids: list[str] = []
    payroll_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        mirror = upsert_payroll_vault_contract(contract)
        company_ids.append(mirror.company_id)
        payroll_ids.append(mirror.payroll_id)
        contract_ids.append(mirror.contract_id)

    _mark_stale_payroll_vaults_archived(company_id=company_id, payroll_id=payroll_id, active_contract_ids=contract_ids)
    return PayrollVaultSyncResult(
        synced_count=len(contract_ids),
        company_ids=company_ids,
        payroll_ids=payroll_ids,
        contract_ids=contract_ids,
    )


def upsert_payroll_vault_contract(contract: dict[str, Any]) -> PayrollVaultMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        PAYROLL_VAULT.module_name,
        PAYROLL_VAULT.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")

    mirror, _created = PayrollVaultMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("companyId") or ""),
            "payroll_id": str(payload.get("payrollId") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "hr_wallet_party": str(payload.get("hrWallet") or ""),
            "employer_wallet_party": str(payload.get("employerWallet") or ""),
            "vault_status": _status_text(payload.get("vaultStatus")),
            "payroll_period": payload.get("payrollPeriod") or {},
            "payroll_token": payload.get("payrollToken") or {},
            "claim_window_start": _parse_optional_datetime(payload.get("claimWindowStart")),
            "claim_window_end": _parse_optional_datetime(payload.get("claimWindowEnd")),
            "totals": {
                "expectedEmployeeCount": payload.get("expectedEmployeeCount"),
                "uploadedAllocationCount": payload.get("uploadedAllocationCount"),
                "allocatedEmployees": payload.get("allocatedEmployees") or [],
                "allocatedEmployeeExternalIds": payload.get("allocatedEmployeeExternalIds") or [],
                "totalNetPay": str(payload.get("totalNetPay") or "0.0000000000"),
                "fundedAmount": str(payload.get("fundedAmount") or "0.0000000000"),
                "settledAmount": str(payload.get("settledAmount") or "0.0000000000"),
                "withdrawnAmount": str(payload.get("withdrawnAmount") or "0.0000000000"),
            },
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )

    LedgerContract.objects.update_or_create(
        contract_id=mirror.contract_id,
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or PAYROLL_VAULT.module_name,
            "entity_name": template_info.get("entity_name") or PAYROLL_VAULT.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return mirror


def sync_salary_allocations(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    parties: list[str] | None = None,
) -> SalaryAllocationSyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync SalaryAllocation contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=SALARY_ALLOCATION,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )
    contracts = _filter_contract_payloads(
        contracts,
        company_id=company_id,
        payroll_id=payroll_id,
        company_key="allocationCompanyId",
        payroll_key="allocationPayrollId",
    )

    company_ids: list[str] = []
    payroll_ids: list[str] = []
    employee_external_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        mirror = upsert_salary_allocation_contract(contract)
        company_ids.append(mirror.company_id)
        payroll_ids.append(mirror.payroll_id)
        employee_external_ids.append(mirror.employee_external_id)
        contract_ids.append(mirror.contract_id)

    _mark_stale_salary_allocations_archived(
        company_id=company_id,
        payroll_id=payroll_id,
        active_contract_ids=contract_ids,
    )
    return SalaryAllocationSyncResult(
        synced_count=len(contract_ids),
        company_ids=company_ids,
        payroll_ids=payroll_ids,
        employee_external_ids=employee_external_ids,
        contract_ids=contract_ids,
    )


def upsert_salary_allocation_contract(contract: dict[str, Any]) -> SalaryAllocationMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        SALARY_ALLOCATION.module_name,
        SALARY_ALLOCATION.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")

    mirror, _created = SalaryAllocationMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("allocationCompanyId") or ""),
            "payroll_id": str(payload.get("allocationPayrollId") or ""),
            "employee_external_id": str(payload.get("employeeExternalId") or ""),
            "employee_wallet_party": str(payload.get("allocationEmployeeWallet") or ""),
            "employer_wallet_party": str(payload.get("allocationEmployerWallet") or ""),
            "hr_wallet_party": str(payload.get("allocationHrWallet") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "allocation_status": _status_text(payload.get("allocationStatus")),
            "salary_breakdown": payload.get("salaryBreakdown") or {},
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )

    LedgerContract.objects.update_or_create(
        contract_id=mirror.contract_id,
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or SALARY_ALLOCATION.module_name,
            "entity_name": template_info.get("entity_name") or SALARY_ALLOCATION.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return mirror


def sync_funding_receipts(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    parties: list[str] | None = None,
) -> FundingReceiptSyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync FundingReceipt contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=FUNDING_RECEIPT,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )
    contracts = _filter_contract_payloads(
        contracts,
        company_id=company_id,
        payroll_id=payroll_id,
        company_key="fundingCompanyId",
        payroll_key="fundingPayrollId",
    )

    company_ids: list[str] = []
    payroll_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        upsert_funding_receipt_contract(contract)
        payload = contract.get("payload") or {}
        company_ids.append(str(payload.get("fundingCompanyId") or ""))
        payroll_ids.append(str(payload.get("fundingPayrollId") or ""))
        contract_ids.append(contract["contract_id"])

    return FundingReceiptSyncResult(
        synced_count=len(contract_ids),
        company_ids=company_ids,
        payroll_ids=payroll_ids,
        contract_ids=contract_ids,
    )


def upsert_funding_receipt_contract(contract: dict[str, Any]) -> LedgerContract:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        FUNDING_RECEIPT.module_name,
        FUNDING_RECEIPT.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")

    ledger_contract, _created = LedgerContract.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or FUNDING_RECEIPT.module_name,
            "entity_name": template_info.get("entity_name") or FUNDING_RECEIPT.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return ledger_contract


def sync_claim_tickets(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    parties: list[str] | None = None,
) -> ClaimTicketSyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync ClaimTicket contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=CLAIM_TICKET,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )
    contracts = _filter_contract_payloads(
        contracts,
        company_id=company_id,
        payroll_id=payroll_id,
        company_key="ticketCompanyId",
        payroll_key="ticketPayrollId",
    )
    if employee_external_id:
        contracts = [
            contract
            for contract in contracts
            if (contract.get("payload") or {}).get("ticketEmployeeExternalId") == employee_external_id
        ]

    company_ids: list[str] = []
    payroll_ids: list[str] = []
    employee_external_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        mirror = upsert_claim_ticket_contract(contract)
        company_ids.append(mirror.company_id)
        payroll_ids.append(mirror.payroll_id)
        employee_external_ids.append(mirror.employee_external_id)
        contract_ids.append(mirror.contract_id)

    _mark_stale_claim_tickets_inactive(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        active_contract_ids=contract_ids,
    )
    return ClaimTicketSyncResult(
        synced_count=len(contract_ids),
        company_ids=company_ids,
        payroll_ids=payroll_ids,
        employee_external_ids=employee_external_ids,
        contract_ids=contract_ids,
    )


def upsert_claim_ticket_contract(contract: dict[str, Any]) -> ClaimTicketMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        CLAIM_TICKET.module_name,
        CLAIM_TICKET.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")

    mirror, _created = ClaimTicketMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("ticketCompanyId") or ""),
            "payroll_id": str(payload.get("ticketPayrollId") or ""),
            "employee_external_id": str(payload.get("ticketEmployeeExternalId") or ""),
            "employee_wallet_party": str(payload.get("ticketEmployeeWallet") or ""),
            "employer_wallet_party": str(payload.get("ticketEmployerWallet") or ""),
            "hr_wallet_party": str(payload.get("ticketHrWallet") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "ticket_amount": str(payload.get("ticketAmount") or "0.0000000000"),
            "ticket_token": payload.get("ticketToken") or {},
            "salary_breakdown": payload.get("ticketSalaryBreakdown") or {},
            "source_allocation_contract_id": str(payload.get("sourceAllocationCid") or ""),
            "claim_window_start": _parse_optional_datetime(payload.get("ticketClaimWindowStart")),
            "claim_window_end": _parse_optional_datetime(payload.get("ticketClaimWindowEnd")),
            "ledger_active": True,
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )

    LedgerContract.objects.update_or_create(
        contract_id=mirror.contract_id,
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or CLAIM_TICKET.module_name,
            "entity_name": template_info.get("entity_name") or CLAIM_TICKET.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return mirror


def sync_salary_claims(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    parties: list[str] | None = None,
) -> SalaryClaimSyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError("At least one read party is required to sync SalaryClaim contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=SALARY_CLAIM,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )
    contracts = _filter_contract_payloads(
        contracts,
        company_id=company_id,
        payroll_id=payroll_id,
        company_key="claimCompanyId",
        payroll_key="claimPayrollId",
    )
    if employee_external_id:
        contracts = [
            contract
            for contract in contracts
            if (contract.get("payload") or {}).get("claimEmployeeExternalId") == employee_external_id
        ]

    company_ids: list[str] = []
    payroll_ids: list[str] = []
    employee_external_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        mirror = upsert_salary_claim_contract(contract)
        company_ids.append(mirror.company_id)
        payroll_ids.append(mirror.payroll_id)
        employee_external_ids.append(mirror.employee_external_id)
        contract_ids.append(mirror.contract_id)

    _mark_stale_salary_claims_archived(
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        active_contract_ids=contract_ids,
    )
    return SalaryClaimSyncResult(
        synced_count=len(contract_ids),
        company_ids=company_ids,
        payroll_ids=payroll_ids,
        employee_external_ids=employee_external_ids,
        contract_ids=contract_ids,
    )


def upsert_salary_claim_contract(contract: dict[str, Any]) -> SalaryClaimMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        SALARY_CLAIM.module_name,
        SALARY_CLAIM.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")

    mirror, _created = SalaryClaimMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("claimCompanyId") or ""),
            "payroll_id": str(payload.get("claimPayrollId") or ""),
            "employee_external_id": str(payload.get("claimEmployeeExternalId") or ""),
            "employee_wallet_party": str(payload.get("claimEmployeeWallet") or ""),
            "employer_wallet_party": str(payload.get("claimEmployerWallet") or ""),
            "hr_wallet_party": str(payload.get("claimHrWallet") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "claim_status": _status_text(payload.get("claimStatus")),
            "claim_amount": str(payload.get("claimAmount") or "0.0000000000"),
            "source_allocation_contract_id": str(payload.get("sourceAllocationCid") or ""),
            "ledger_active": True,
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )

    _upsert_ledger_contract(
        contract=contract,
        template=SALARY_CLAIM,
        template_display_id=template_display_id,
        payload=payload,
        ledger_offset=ledger_offset,
    )
    return mirror


def sync_settlement_receipts(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    parties: list[str] | None = None,
) -> AuditContractSyncResult:
    return _sync_audit_contracts(
        record_type="settlement_receipt",
        template=SETTLEMENT_RECEIPT,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        company_key="receiptCompanyId",
        payroll_key="receiptPayrollId",
        employee_key="receiptEmployeeExternalId",
        upsert_fn=upsert_settlement_receipt_contract,
        parties=parties,
    )


def upsert_settlement_receipt_contract(contract: dict[str, Any]) -> SettlementReceiptMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        SETTLEMENT_RECEIPT.module_name,
        SETTLEMENT_RECEIPT.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")
    mirror, _created = SettlementReceiptMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("receiptCompanyId") or ""),
            "payroll_id": str(payload.get("receiptPayrollId") or ""),
            "employee_external_id": str(payload.get("receiptEmployeeExternalId") or ""),
            "employee_wallet_party": str(payload.get("receiptEmployeeWallet") or ""),
            "employer_wallet_party": str(payload.get("receiptEmployerWallet") or ""),
            "hr_wallet_party": str(payload.get("receiptHrWallet") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "amount": str(payload.get("receiptAmount") or "0.0000000000"),
            "settlement_reference": str(payload.get("settlementReference") or ""),
            "settlement_proof": payload.get("settlementProof") or {},
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )
    _upsert_ledger_contract(
        contract=contract,
        template=SETTLEMENT_RECEIPT,
        template_display_id=template_display_id,
        payload=payload,
        ledger_offset=ledger_offset,
    )
    return mirror


def sync_payslips(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    parties: list[str] | None = None,
) -> AuditContractSyncResult:
    return _sync_audit_contracts(
        record_type="payslip",
        template=PAYSLIP,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        company_key="payslipCompanyId",
        payroll_key="payslipPayrollId",
        employee_key="payslipEmployeeExternalId",
        upsert_fn=upsert_payslip_contract,
        parties=parties,
    )


def upsert_payslip_contract(contract: dict[str, Any]) -> PayslipMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(template_info, PAYSLIP.module_name, PAYSLIP.entity_name)
    ledger_offset = str(contract.get("ledger_offset") or "")
    mirror, _created = PayslipMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("payslipCompanyId") or ""),
            "payroll_id": str(payload.get("payslipPayrollId") or ""),
            "employee_external_id": str(payload.get("payslipEmployeeExternalId") or ""),
            "employee_wallet_party": str(payload.get("payslipEmployeeWallet") or ""),
            "employer_wallet_party": str(payload.get("payslipEmployerWallet") or ""),
            "hr_wallet_party": str(payload.get("payslipHrWallet") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "salary_breakdown": payload.get("payslipSalaryBreakdown") or {},
            "settlement_proof": payload.get("settlementProof") or {},
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )
    _upsert_ledger_contract(
        contract=contract,
        template=PAYSLIP,
        template_display_id=template_display_id,
        payload=payload,
        ledger_offset=ledger_offset,
    )
    return mirror


def sync_settled_salary_records(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    parties: list[str] | None = None,
) -> AuditContractSyncResult:
    return _sync_audit_contracts(
        record_type="settled_salary_record",
        template=SETTLED_SALARY_RECORD,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        company_key="settledRecordCompanyId",
        payroll_key="settledRecordPayrollId",
        employee_key="settledRecordEmployeeExternalId",
        upsert_fn=upsert_settled_salary_record_contract,
        parties=parties,
    )


def upsert_settled_salary_record_contract(contract: dict[str, Any]) -> SettledSalaryRecordMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        SETTLED_SALARY_RECORD.module_name,
        SETTLED_SALARY_RECORD.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")
    mirror, _created = SettledSalaryRecordMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("settledRecordCompanyId") or ""),
            "payroll_id": str(payload.get("settledRecordPayrollId") or ""),
            "employee_external_id": str(payload.get("settledRecordEmployeeExternalId") or ""),
            "employee_wallet_party": str(payload.get("settledRecordEmployeeWallet") or ""),
            "employer_wallet_party": str(payload.get("settledRecordEmployerWallet") or ""),
            "hr_wallet_party": str(payload.get("settledRecordHrWallet") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "amount": str(payload.get("settledRecordAmount") or "0.0000000000"),
            "settlement_reference": str(payload.get("settledRecordReference") or ""),
            "settlement_proof": payload.get("settlementProof") or {},
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )
    _upsert_ledger_contract(
        contract=contract,
        template=SETTLED_SALARY_RECORD,
        template_display_id=template_display_id,
        payload=payload,
        ledger_offset=ledger_offset,
    )
    return mirror


def sync_failed_salary_claims(
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    employee_external_id: str | None = None,
    parties: list[str] | None = None,
) -> AuditContractSyncResult:
    return _sync_audit_contracts(
        record_type="failed_salary_claim",
        template=FAILED_SALARY_CLAIM,
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
        company_key="failedCompanyId",
        payroll_key="failedPayrollId",
        employee_key="failedEmployeeExternalId",
        upsert_fn=upsert_failed_salary_claim_contract,
        parties=parties,
    )


def upsert_failed_salary_claim_contract(contract: dict[str, Any]) -> FailedSalaryClaimMirror:
    payload = contract.get("payload") or {}
    template_info = contract.get("template_id") or {}
    template_display_id = _template_display_id_for(
        template_info,
        FAILED_SALARY_CLAIM.module_name,
        FAILED_SALARY_CLAIM.entity_name,
    )
    ledger_offset = str(contract.get("ledger_offset") or "")
    mirror, _created = FailedSalaryClaimMirror.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "company_id": str(payload.get("failedCompanyId") or ""),
            "payroll_id": str(payload.get("failedPayrollId") or ""),
            "employee_external_id": str(payload.get("failedEmployeeExternalId") or ""),
            "employee_wallet_party": str(payload.get("failedEmployeeWallet") or ""),
            "employer_wallet_party": str(payload.get("failedEmployerWallet") or ""),
            "hr_wallet_party": str(payload.get("failedHrWallet") or ""),
            "company_admin_party": str(payload.get("companyAdmin") or ""),
            "amount": str(payload.get("failedAmount") or "0.0000000000"),
            "failure_reason": str(payload.get("failureReason") or ""),
            "payload": payload,
            "ledger_offset": ledger_offset,
        },
    )
    _upsert_ledger_contract(
        contract=contract,
        template=FAILED_SALARY_CLAIM,
        template_display_id=template_display_id,
        payload=payload,
        ledger_offset=ledger_offset,
    )
    return mirror


def apply_created_event(event: dict[str, Any]) -> None:
    # TODO: Map a created event into LedgerContract and a template-specific mirror.
    raise LedgerNotImplementedError("Created-event application is not implemented yet.")


def apply_archived_event(event: dict[str, Any]) -> None:
    # TODO: Mark LedgerContract and template-specific mirrors inactive when archived.
    raise LedgerNotImplementedError("Archived-event application is not implemented yet.")


def _filter_contract_payloads(
    contracts: list[dict[str, Any]],
    *,
    company_id: str | None = None,
    payroll_id: str | None = None,
    company_key: str = "companyId",
    payroll_key: str = "payrollId",
) -> list[dict[str, Any]]:
    filtered = contracts
    if company_id:
        filtered = [
            contract for contract in filtered if (contract.get("payload") or {}).get(company_key) == company_id
        ]
    if payroll_id:
        filtered = [
            contract for contract in filtered if (contract.get("payload") or {}).get(payroll_key) == payroll_id
        ]
    return filtered


def _sync_audit_contracts(
    *,
    record_type: str,
    template,
    company_id: str | None,
    payroll_id: str | None,
    employee_external_id: str | None,
    company_key: str,
    payroll_key: str,
    employee_key: str,
    upsert_fn,
    parties: list[str] | None,
) -> AuditContractSyncResult:
    read_parties = parties or default_read_parties()
    if not read_parties:
        raise LedgerSyncError(f"At least one read party is required to sync {record_type} contracts.")

    settings = load_ledger_auth_settings()
    client = LedgerClient(settings)
    contracts = client.query_active_contracts(
        template=template,
        parties=read_parties,
        allow_wildcard_fallback=False,
    )
    contracts = _filter_contract_payloads(
        contracts,
        company_id=company_id,
        payroll_id=payroll_id,
        company_key=company_key,
        payroll_key=payroll_key,
    )
    if employee_external_id:
        contracts = [
            contract
            for contract in contracts
            if (contract.get("payload") or {}).get(employee_key) == employee_external_id
        ]

    company_ids: list[str] = []
    payroll_ids: list[str] = []
    employee_external_ids: list[str] = []
    contract_ids: list[str] = []
    for contract in contracts:
        mirror = upsert_fn(contract)
        company_ids.append(mirror.company_id)
        payroll_ids.append(mirror.payroll_id)
        employee_external_ids.append(mirror.employee_external_id)
        contract_ids.append(mirror.contract_id)

    return AuditContractSyncResult(
        record_type=record_type,
        synced_count=len(contract_ids),
        company_ids=company_ids,
        payroll_ids=payroll_ids,
        employee_external_ids=employee_external_ids,
        contract_ids=contract_ids,
    )


def _upsert_ledger_contract(
    *,
    contract: dict[str, Any],
    template,
    template_display_id: str,
    payload: dict[str, Any],
    ledger_offset: str,
) -> LedgerContract:
    template_info = contract.get("template_id") or {}
    ledger_contract, _created = LedgerContract.objects.update_or_create(
        contract_id=contract["contract_id"],
        defaults={
            "template_id": template_display_id,
            "module_name": template_info.get("module_name") or template.module_name,
            "entity_name": template_info.get("entity_name") or template.entity_name,
            "payload": payload,
            "contract_key": contract.get("contract_key"),
            "signatories": contract.get("signatories") or [],
            "observers": contract.get("observers") or [],
            "active": True,
            "ledger_offset": ledger_offset,
        },
    )
    return ledger_contract


def _status_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        tag = value.get("tag") or value.get("constructor")
        if tag:
            return str(tag)
    return str(value or "")


def _mark_stale_payroll_vaults_archived(
    *,
    company_id: str | None,
    payroll_id: str | None,
    active_contract_ids: list[str],
) -> None:
    if not company_id and not payroll_id:
        return
    queryset = PayrollVaultMirror.objects.exclude(vault_status="Archived")
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if payroll_id:
        queryset = queryset.filter(payroll_id=payroll_id)
    if active_contract_ids:
        queryset = queryset.exclude(contract_id__in=active_contract_ids)
    stale_ids = list(queryset.values_list("contract_id", flat=True))
    if not stale_ids:
        return
    queryset.update(vault_status="Archived")
    LedgerContract.objects.filter(contract_id__in=stale_ids).update(active=False)


def _mark_stale_salary_claims_archived(
    *,
    company_id: str | None,
    payroll_id: str | None,
    employee_external_id: str | None,
    active_contract_ids: list[str],
) -> None:
    if not company_id and not payroll_id and not employee_external_id:
        return
    queryset = SalaryClaimMirror.objects.exclude(claim_status="Archived")
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if payroll_id:
        queryset = queryset.filter(payroll_id=payroll_id)
    if employee_external_id:
        queryset = queryset.filter(employee_external_id=employee_external_id)
    if active_contract_ids:
        queryset = queryset.exclude(contract_id__in=active_contract_ids)
    stale_ids = list(queryset.values_list("contract_id", flat=True))
    if not stale_ids:
        return
    queryset.update(claim_status="Archived", ledger_active=False)
    LedgerContract.objects.filter(contract_id__in=stale_ids).update(active=False)


def _mark_stale_claim_tickets_inactive(
    *,
    company_id: str | None,
    payroll_id: str | None,
    employee_external_id: str | None,
    active_contract_ids: list[str],
) -> None:
    if not company_id and not payroll_id and not employee_external_id:
        return
    queryset = ClaimTicketMirror.objects.filter(ledger_active=True)
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if payroll_id:
        queryset = queryset.filter(payroll_id=payroll_id)
    if employee_external_id:
        queryset = queryset.filter(employee_external_id=employee_external_id)
    if active_contract_ids:
        queryset = queryset.exclude(contract_id__in=active_contract_ids)
    stale_ids = list(queryset.values_list("contract_id", flat=True))
    if not stale_ids:
        return
    queryset.update(ledger_active=False)
    LedgerContract.objects.filter(contract_id__in=stale_ids).update(active=False)


def _mark_stale_salary_allocations_archived(
    *,
    company_id: str | None,
    payroll_id: str | None,
    active_contract_ids: list[str],
) -> None:
    if not company_id and not payroll_id:
        return
    queryset = SalaryAllocationMirror.objects.exclude(allocation_status="Archived")
    if company_id:
        queryset = queryset.filter(company_id=company_id)
    if payroll_id:
        queryset = queryset.filter(payroll_id=payroll_id)
    if active_contract_ids:
        queryset = queryset.exclude(contract_id__in=active_contract_ids)
    stale_ids = list(queryset.values_list("contract_id", flat=True))
    if not stale_ids:
        return
    queryset.update(allocation_status="Archived")
    LedgerContract.objects.filter(contract_id__in=stale_ids).update(active=False)
