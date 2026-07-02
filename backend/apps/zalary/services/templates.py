from dataclasses import dataclass
import os

from .auth import DAML_PACKAGE_ID, DAML_PACKAGE_NAME


DEFAULT_PACKAGE_NAME = "zalary-usdcx-contracts"
DEFAULT_PACKAGE_ID = "5ae1c32a3351f951e52a4a41205aa18818b55d9bb9f129327ffc9311d4460094"


@dataclass(frozen=True)
class TemplateRef:
    module_name: str
    entity_name: str

    def identifier(self, package_id: str | None = None) -> dict[str, str]:
        resolved_package_id = (
            package_id
            or os.environ.get(DAML_PACKAGE_ID, "")
            or DEFAULT_PACKAGE_ID
        )
        return {
            "packageId": resolved_package_id,
            "moduleName": self.module_name,
            "entityName": self.entity_name,
        }

    def display_id(self, package_id: str | None = None) -> str:
        package = package_id or os.environ.get(DAML_PACKAGE_ID) or os.environ.get(DAML_PACKAGE_NAME) or DEFAULT_PACKAGE_NAME
        return f"{package}:{self.module_name}:{self.entity_name}"


ZALARY_CONFIG = TemplateRef("Zalary.Platform", "ZalaryConfig")
COMPANY = TemplateRef("Zalary.Company", "Company")
EMPLOYEE_ENROLLMENT = TemplateRef("Zalary.Enrollment", "EmployeeEnrollment")
CLAIM_ACTION_AUTHORIZATION = TemplateRef("Zalary.Payroll", "ClaimActionAuthorization")
PAYROLL_VAULT = TemplateRef("Zalary.Payroll", "PayrollVault")
SALARY_ALLOCATION = TemplateRef("Zalary.Payroll", "SalaryAllocation")
CLAIM_TICKET = TemplateRef("Zalary.Payroll", "ClaimTicket")
SALARY_CLAIM = TemplateRef("Zalary.Payroll", "SalaryClaim")
FUNDING_RECEIPT = TemplateRef("Zalary.Audit", "FundingReceipt")
PAYROLL_CANCELLATION_RECEIPT = TemplateRef("Zalary.Audit", "PayrollCancellationReceipt")
LEFTOVER_WITHDRAWAL_RECEIPT = TemplateRef("Zalary.Audit", "LeftoverWithdrawalReceipt")
SETTLEMENT_RECEIPT = TemplateRef("Zalary.Audit", "SettlementReceipt")
PAYSLIP = TemplateRef("Zalary.Audit", "Payslip")
SETTLED_SALARY_RECORD = TemplateRef("Zalary.Audit", "SettledSalaryRecord")
FAILED_SALARY_CLAIM = TemplateRef("Zalary.Audit", "FailedSalaryClaim")
ZUSD_ISSUER = TemplateRef("Zalary.Sandbox.ZUSD", "ZUSDIssuer")
ZUSD_HOLDING = TemplateRef("Zalary.Sandbox.ZUSD", "ZUSDHolding")
ZUSD_FAUCET_GRANT = TemplateRef("Zalary.Sandbox.ZUSD", "ZUSDFaucetGrant")
ZUSD_FAUCET_CONFIG = TemplateRef("Zalary.Sandbox.ZUSD", "ZUSDFaucetConfig")

TEMPLATES = {
    "ZalaryConfig": ZALARY_CONFIG,
    "Company": COMPANY,
    "EmployeeEnrollment": EMPLOYEE_ENROLLMENT,
    "ClaimActionAuthorization": CLAIM_ACTION_AUTHORIZATION,
    "PayrollVault": PAYROLL_VAULT,
    "SalaryAllocation": SALARY_ALLOCATION,
    "ClaimTicket": CLAIM_TICKET,
    "SalaryClaim": SALARY_CLAIM,
    "FundingReceipt": FUNDING_RECEIPT,
    "PayrollCancellationReceipt": PAYROLL_CANCELLATION_RECEIPT,
    "LeftoverWithdrawalReceipt": LEFTOVER_WITHDRAWAL_RECEIPT,
    "SettlementReceipt": SETTLEMENT_RECEIPT,
    "Payslip": PAYSLIP,
    "SettledSalaryRecord": SETTLED_SALARY_RECORD,
    "FailedSalaryClaim": FAILED_SALARY_CLAIM,
    "ZUSDIssuer": ZUSD_ISSUER,
    "ZUSDHolding": ZUSD_HOLDING,
    "ZUSDFaucetGrant": ZUSD_FAUCET_GRANT,
    "ZUSDFaucetConfig": ZUSD_FAUCET_CONFIG,
}

CHOICES = {
    "ZalaryConfig": {
        "UpdateSupportedTokens": "UpdateSupportedTokens",
        "DeactivatePlatformConfig": "DeactivatePlatformConfig",
        "CreateCompany": "CreateCompany",
        "UpdateCompanyAllowedTokens": "UpdateCompanyAllowedTokens",
    },
    "Company": {
        "AddHR": "AddHR",
        "RemoveHR": "RemoveHR",
        "AddEmployerWallet": "AddEmployerWallet",
        "RemoveEmployerWallet": "RemoveEmployerWallet",
        "CreateEmployeeEnrollment": "CreateEmployeeEnrollment",
        "CreatePayrollVault": "CreatePayrollVault",
    },
    "EmployeeEnrollment": {
        "DeactivateEnrollmentByHR": "DeactivateEnrollmentByHR",
    },
    "ClaimActionAuthorization": {
        "ConsumeClaimActionAuthorization": "ConsumeClaimActionAuthorization",
    },
    "PayrollVault": {
        "AddSalaryAllocation": "AddSalaryAllocation",
        "FinalizeAllocations": "FinalizeAllocations",
        "ConfirmFunding": "ConfirmFunding",
        "ActivatePayroll": "ActivatePayroll",
        "RecordSettlement": "RecordSettlement",
        "ClosePayrollByHR": "ClosePayrollByHR",
        "ClosePayrollByEmployer": "ClosePayrollByEmployer",
        "CancelPayrollBeforeFunding": "CancelPayrollBeforeFunding",
        "CancelPayrollAfterFunding": "CancelPayrollAfterFunding",
        "WithdrawLeftovers": "WithdrawLeftovers",
    },
    "SalaryAllocation": {
        "IssueClaimTicket": "IssueClaimTicket",
        "MarkSettled": "MarkSettled",
        "MarkRejected": "MarkRejected",
    },
    "ClaimTicket": {
        "RequestSalaryClaim": "RequestSalaryClaim",
    },
    "SalaryClaim": {
        "ConfirmSalarySettlement": "ConfirmSalarySettlement",
        "RejectSalaryClaim": "RejectSalaryClaim",
    },
    "ZUSDIssuer": {
        "MintZUSD": "MintZUSD",
    },
    "ZUSDHolding": {
        "TransferZUSD": "TransferZUSD",
    },
    "ZUSDFaucetConfig": {
        "UpdateZUSDFaucetLimits": "UpdateZUSDFaucetLimits",
        "DisableZUSDFaucet": "DisableZUSDFaucet",
        "EnableZUSDFaucet": "EnableZUSDFaucet",
    },
}
