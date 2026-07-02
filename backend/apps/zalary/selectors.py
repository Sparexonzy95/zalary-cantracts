from django.db.models import QuerySet

from .models import (
    CompanyMirror,
    EmployeeEnrollmentMirror,
    LedgerCommand,
    LedgerContract,
    PayrollVaultMirror,
    SalaryAllocationMirror,
    SalaryClaimMirror,
    ZalaryConfigMirror,
    ZUSDFaucetRequest,
    ZUSDHoldingMirror,
)


def active_contracts(template_id: str | None = None) -> QuerySet[LedgerContract]:
    queryset = LedgerContract.objects.filter(active=True)
    if template_id:
        queryset = queryset.filter(template_id=template_id)
    return queryset.order_by("template_id", "contract_id")


def latest_platform_config() -> ZalaryConfigMirror | None:
    return ZalaryConfigMirror.objects.order_by("-synced_at").first()


def active_platform_config() -> ZalaryConfigMirror | None:
    return ZalaryConfigMirror.objects.filter(is_active=True, ledger_active=True).order_by("-synced_at").first()


def synced_zalary_configs(*, include_inactive: bool = True) -> QuerySet[ZalaryConfigMirror]:
    queryset = ZalaryConfigMirror.objects.all()
    if not include_inactive:
        queryset = queryset.filter(ledger_active=True)
    return queryset.order_by("-ledger_active", "-synced_at", "contract_id")


def companies_for_party(party_id: str) -> QuerySet[CompanyMirror]:
    return CompanyMirror.objects.filter(
        company_admin_party=party_id
    ) | CompanyMirror.objects.filter(
        admin_wallet_parties__contains=[party_id]
    ) | CompanyMirror.objects.filter(
        hr_wallet_parties__contains=[party_id]
    ) | CompanyMirror.objects.filter(
        employer_wallet_parties__contains=[party_id]
    )


def payroll_vaults_for_company(company_id: str) -> QuerySet[PayrollVaultMirror]:
    return PayrollVaultMirror.objects.filter(company_id=company_id).order_by("-synced_at")


def enrollments_for_company(company_id: str) -> QuerySet[EmployeeEnrollmentMirror]:
    return EmployeeEnrollmentMirror.objects.filter(company_id=company_id).order_by(
        "employee_external_id"
    )


def allocations_for_payroll(company_id: str, payroll_id: str) -> QuerySet[SalaryAllocationMirror]:
    return SalaryAllocationMirror.objects.filter(
        company_id=company_id,
        payroll_id=payroll_id,
    ).order_by("employee_external_id")


def claims_for_payroll(company_id: str, payroll_id: str) -> QuerySet[SalaryClaimMirror]:
    return SalaryClaimMirror.objects.filter(
        company_id=company_id,
        payroll_id=payroll_id,
    ).order_by("employee_external_id")


def commands_by_status(status: str) -> QuerySet[LedgerCommand]:
    return LedgerCommand.objects.filter(status=status).order_by("created_at")


def zusd_holdings_for_owner(owner_party: str) -> QuerySet[ZUSDHoldingMirror]:
    return ZUSDHoldingMirror.objects.filter(
        owner_party=owner_party,
        symbol="ZUSD",
        ledger_active=True,
    ).order_by("contract_id")


def zusd_faucet_requests_for_owner(owner_party: str | None = None) -> QuerySet[ZUSDFaucetRequest]:
    queryset = ZUSDFaucetRequest.objects.all()
    if owner_party:
        queryset = queryset.filter(owner_party=owner_party)
    return queryset.order_by("-created_at")
