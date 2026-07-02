import os
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.zalary.models import (
    ClaimTicketMirror,
    CommandStatus,
    CompanyMirror,
    EmployeeEnrollmentMirror,
    FaucetRequestStatus,
    LedgerCommand,
    LedgerParty,
    LedgerRole,
    PayrollVaultMirror,
    SalaryAllocationMirror,
    SalaryClaimMirror,
    SettlementReceiptMirror,
    USDCxTransferRecord,
    ZalaryConfigMirror,
    ZUSDFaucetRequest,
)
from apps.zalary.services.commands import create_company
from apps.zalary.services import enrollment as enrollment_service
from apps.zalary.services import payroll as payroll_service
from apps.zalary.services.enrollment import create_employee_enrollment, preflight_employee_enrollment
from apps.zalary.services.errors import (
    ConfigurationError,
    DuplicateCompanyError,
    DuplicateEnrollmentError,
    DuplicateClaimTicketError,
    DuplicatePayrollVaultError,
    DuplicateSalaryAllocationError,
    DuplicateSalaryClaimError,
    DuplicateSettlementError,
    LedgerSubmissionError,
    OnboardingValidationError,
    SettlementProofError,
)
from apps.zalary.services.faucet import get_zusd_balance, request_zusd_faucet_mint
from apps.zalary.services.ledger import LedgerCommandResult
from apps.zalary.services.payloads import (
    activate_payroll_choice_payload,
    add_salary_allocation_choice_payload,
    confirm_salary_settlement_choice_payload,
    confirm_funding_choice_payload,
    create_company_choice_payload,
    create_employee_enrollment_choice_payload,
    create_payroll_vault_choice_payload,
    issue_claim_ticket_choice_payload,
    request_salary_claim_choice_payload,
)
from apps.zalary.services.payroll import (
    activate_payroll,
    confirm_funding,
    create_demo_funding_activation_ticket_pipeline,
    create_demo_payroll_pipeline,
    create_payroll_vault,
    issue_claim_ticket,
    preflight_activate_payroll,
    preflight_confirm_funding,
    preflight_finalize_allocations,
    preflight_issue_claim_ticket,
    preflight_payroll_vault_creation,
    preflight_salary_allocation,
)
from apps.zalary.services.roles import register_ledger_party
from apps.zalary.services.settlement import (
    confirm_salary_settlement,
    create_demo_full_payroll_execution,
    demo_settlement_proof,
    preflight_request_salary_claim,
    request_salary_claim,
    validate_settlement_proof,
)
from apps.zalary.services.idempotency import key_confirm_settlement, key_request_salary_claim
from apps.zalary.services.sync import sync_claim_tickets, sync_employee_enrollments, sync_salary_claims
from apps.zalary.services.templates import CLAIM_TICKET, COMPANY, SALARY_CLAIM, ZUSD_HOLDING
from apps.zalary.services.token_transfers.base import (
    BaseTokenTransferProvider,
    TokenTransferRequest,
    TokenTransferResult,
    TRANSFER_COMPLETED,
    TRANSFER_FAILED,
    TRANSFER_PENDING,
    TRANSFER_UNAVAILABLE,
)
from apps.zalary.services.token_transfers.usdcx import (
    ConfiguredUSDCxTransferProvider,
    DEFAULT_HOLDING_INTERFACE_ID,
    DEFAULT_USDCX_INSTRUMENT_ADMIN,
    P2PLENDING_PROVIDER_MODE,
    P2PLENDING_SCHEMA,
    P2PLENDING_USDCX_PACKAGE_ID,
    P2PLENDING_USDCX_MODULE,
    P2PLENDING_USDCX_HOLDING_TEMPLATE,
    P2PLENDING_USDCX_REGISTRY_TEMPLATE,
    P2PLENDING_SPLIT_CHOICE,
    P2PLendingTransferPlan,
    TransferFactoryDiscovery,
    TRANSFER_ARGUMENT_SHAPE_CANONICAL_FLAT,
    TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS,
    USDCX_INSUFFICIENT_HOLDINGS_ERROR,
)
from apps.zalary.services.token_transfers.zusd import (
    ConfiguredZUSDTransferProvider,
    ZALARY_TEST_TOKEN_PROVIDER_MODE,
    ZUSDHoldingCandidate,
    ZUSD_INSUFFICIENT_HOLDINGS_ERROR,
    select_zusd_holding,
    zusd_token_instrument,
)
from apps.zalary.services.token_transfers.factory import get_token_transfer_provider


PARTY = "5nsandbox-devnet-2::1220a14ca128063b8dc9d1ebb0bd22633be9f2168500f4dbc1ecaeb1855b14e5acf8"
OTHER_PARTY = "5nsandbox-devnet-2::1220bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
EMPLOYEE_PARTY = "5nsandbox-devnet-2::1220cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
TOKEN = {
    "symbol": "USDCx",
    "instrumentId": "USDCx",
    "instrumentAdmin": "decentralized-usdc-interchain-rep::122049e2af8a725bd19759320fc83c638e7718973eac189d8f201309c512d1ffec61",
    "utilityApiUrl": "https://api.utilities.digitalasset-staging.com",
    "xReserveApiUrl": "https://xreserve-api-testnet.circle.com",
}
ZUSD_TOKEN = {
    "symbol": "ZUSD",
    "instrumentId": "ZUSD",
    "instrumentAdmin": PARTY,
    "utilityApiUrl": "zalary://sandbox/zusd",
    "xReserveApiUrl": "zalary://sandbox/zusd",
}


def create_platform_config() -> ZalaryConfigMirror:
    return ZalaryConfigMirror.objects.create(
        contract_id="00platformconfig",
        package_name="zalary-usdcx-contracts",
        template_id="Zalary.Platform:ZalaryConfig",
        platform_admin_party=PARTY,
        supported_tokens=[TOKEN],
        default_token=TOKEN,
        is_active=True,
        ledger_active=True,
        payload={
            "platformAdmin": PARTY,
            "supportedTokens": [TOKEN],
            "defaultToken": TOKEN,
            "isActive": True,
        },
    )


def create_company_mirror(*, company_id: str = "zalary-demo-001") -> CompanyMirror:
    return CompanyMirror.objects.create(
        contract_id=f"00company-{company_id}",
        company_id=company_id,
        company_name="Zalary Demo Company",
        platform_admin_party=PARTY,
        company_admin_party=PARTY,
        admin_wallet_parties=[PARTY],
        hr_wallet_parties=[PARTY],
        employer_wallet_parties=[PARTY],
        allowed_tokens=[TOKEN],
        payload={
            "platformAdmin": PARTY,
            "companyAdmin": PARTY,
            "companyName": "Zalary Demo Company",
            "companyId": company_id,
            "adminWallets": [PARTY],
            "hrWallets": [PARTY],
            "employerWallets": [PARTY],
            "allowedTokens": [TOKEN],
        },
    )


def create_enrollment_mirror(*, company_id: str = "zalary-demo-001") -> EmployeeEnrollmentMirror:
    return EmployeeEnrollmentMirror.objects.create(
        contract_id=f"00enrollment-{company_id}",
        company_id=company_id,
        company_admin_party=PARTY,
        hr_wallet_party=PARTY,
        employer_wallet_party=PARTY,
        employee_wallet_party=EMPLOYEE_PARTY,
        employee_external_id="EMP-001",
        is_active=True,
    )


def create_payroll_vault_mirror(
    *,
    company_id: str = "zalary-demo-001",
    payroll_id: str = "payroll-001",
    status: str = "Created",
    uploaded_count: int = 0,
    expected_count: int = 1,
    total_net_pay: str = "0.0000000000",
    claim_window_start=None,
    claim_window_end=None,
) -> PayrollVaultMirror:
    claim_window_start = claim_window_start or (timezone.now() - timedelta(minutes=5))
    claim_window_end = claim_window_end or (timezone.now() + timedelta(days=30))
    return PayrollVaultMirror.objects.create(
        contract_id=f"00vault-{company_id}-{payroll_id}-{status}-{uploaded_count}",
        company_id=company_id,
        payroll_id=payroll_id,
        company_admin_party=PARTY,
        hr_wallet_party=PARTY,
        employer_wallet_party=PARTY,
        vault_status=status,
        payroll_period={"label": payroll_id, "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
        payroll_token=TOKEN,
        claim_window_start=claim_window_start,
        claim_window_end=claim_window_end,
        totals={
            "expectedEmployeeCount": expected_count,
            "uploadedAllocationCount": uploaded_count,
            "allocatedEmployees": [EMPLOYEE_PARTY] if uploaded_count else [],
            "allocatedEmployeeExternalIds": ["EMP-001"] if uploaded_count else [],
            "totalNetPay": total_net_pay,
        },
        payload={
            "expectedEmployeeCount": expected_count,
            "uploadedAllocationCount": uploaded_count,
            "totalNetPay": total_net_pay,
        },
    )


def create_salary_allocation_mirror(
    *,
    company_id: str = "zalary-demo-001",
    payroll_id: str = "payroll-001",
    status: str = "AllocationCreated",
) -> SalaryAllocationMirror:
    return SalaryAllocationMirror.objects.create(
        contract_id=f"00allocation-{company_id}-{payroll_id}",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id="EMP-001",
        employee_wallet_party=EMPLOYEE_PARTY,
        employer_wallet_party=PARTY,
        hr_wallet_party=PARTY,
        company_admin_party=PARTY,
        allocation_status=status,
        salary_breakdown=salary_payload(),
    )


def create_claim_ticket_mirror(
    *,
    company_id: str = "zalary-demo-001",
    payroll_id: str = "payroll-001",
) -> ClaimTicketMirror:
    return ClaimTicketMirror.objects.create(
        contract_id=f"00ticket-{company_id}-{payroll_id}",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id="EMP-001",
        employee_wallet_party=EMPLOYEE_PARTY,
        employer_wallet_party=PARTY,
        hr_wallet_party=PARTY,
        company_admin_party=PARTY,
        ticket_amount="1100.0000000000",
        ticket_token=TOKEN,
        salary_breakdown=salary_payload(),
        source_allocation_contract_id=f"00allocation-{company_id}-{payroll_id}",
        claim_window_start=timezone.now() - timedelta(minutes=5),
        claim_window_end=timezone.now() + timedelta(days=30),
    )


def create_salary_claim_mirror(
    *,
    company_id: str = "zalary-demo-001",
    payroll_id: str = "payroll-001",
    status: str = "ClaimRequested",
) -> SalaryClaimMirror:
    source_allocation_contract_id = f"00allocation-{company_id}-{payroll_id}"
    return SalaryClaimMirror.objects.create(
        contract_id=f"00claim-{company_id}-{payroll_id}",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id="EMP-001",
        employee_wallet_party=EMPLOYEE_PARTY,
        employer_wallet_party=PARTY,
        hr_wallet_party=PARTY,
        company_admin_party=PARTY,
        claim_status=status,
        claim_amount="1100.0000000000",
        source_allocation_contract_id=source_allocation_contract_id,
        payload={
            "claimHrWallet": PARTY,
            "claimEmployerWallet": PARTY,
            "claimEmployeeWallet": EMPLOYEE_PARTY,
            "claimCompanyId": company_id,
            "claimPayrollId": payroll_id,
            "claimPeriod": {"label": payroll_id, "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
            "claimToken": TOKEN,
            "claimEmployeeExternalId": "EMP-001",
            "claimSalaryBreakdown": salary_payload(),
            "claimAmount": "1100.0000000000",
            "sourceAllocationCid": source_allocation_contract_id,
            "claimStatus": status,
            "companyAdmin": PARTY,
        },
    )


def create_settlement_receipt_mirror(
    *,
    company_id: str = "zalary-demo-001",
    payroll_id: str = "payroll-001",
    settlement_reference: str = "SETTLE-payroll-001",
) -> SettlementReceiptMirror:
    proof = {
        "token": TOKEN,
        "sender": PARTY,
        "receiver": EMPLOYEE_PARTY,
        "amount": "1100.0000000000",
        "transferReference": settlement_reference,
        "transferInstructionCid": None,
        "holdingCid": None,
        "executedAt": timezone.now().isoformat(),
    }
    return SettlementReceiptMirror.objects.create(
        contract_id=f"00receipt-{company_id}-{payroll_id}",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id="EMP-001",
        employee_wallet_party=EMPLOYEE_PARTY,
        employer_wallet_party=PARTY,
        hr_wallet_party=PARTY,
        company_admin_party=PARTY,
        amount="1100.0000000000",
        settlement_reference=settlement_reference,
        settlement_proof=proof,
    )


def salary_payload() -> dict:
    return {
        "grossPay": "1000.0000000000",
        "allowances": "200.0000000000",
        "deductions": "100.0000000000",
        "netPay": "1100.0000000000",
        "token": TOKEN,
    }


def token_transfer_request(*, amount: str = "1100.0000000000") -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id="zalary-demo-001",
        payroll_id="payroll-001",
        employee_external_id="EMP-001",
        salary_claim_contract_id="00claim-zalary-demo-001-payroll-001",
        token=TOKEN,
        sender_party=PARTY,
        receiver_party=EMPLOYEE_PARTY,
        amount=amount,
        transfer_reference="SETTLE-payroll-001",
    )


def holding_contract(
    contract_id: str,
    *,
    owner: str = PARTY,
    amount: str = "1100.0000000000",
    instrument_id: str = "USDCx",
    instrument_admin: str = TOKEN["instrumentAdmin"],
    locked=False,
) -> dict:
    view = {
        "owner": owner,
        "instrumentId": {"id": instrument_id, "admin": instrument_admin},
        "amount": amount,
    }
    if locked:
        view["lock"] = {"tag": "Locked", "value": {"reason": "test"}}
    return {
        "contract_id": contract_id,
        "interface_views": {DEFAULT_HOLDING_INTERFACE_ID: view},
        "payload": {},
        "created_event_blob": f"blob-{contract_id}",
    }


def p2p_holding_contract(
    contract_id: str,
    *,
    owner: str = PARTY,
    amount: str = "1.0000000000",
    registry: str = PARTY,
    locked=False,
) -> dict:
    payload = {
        "owner": owner,
        "amount": amount,
        "registry": registry,
        "lock": {"tag": "None", "value": None} if not locked else {"tag": "Locked", "value": {}},
        "meta": {"values": {}},
        "holdingObservers": [],
    }
    return {
        "contract_id": contract_id,
        "template_id": {
            "package_id": P2PLENDING_USDCX_PACKAGE_ID,
            "module_name": P2PLENDING_USDCX_MODULE,
            "entity_name": P2PLENDING_USDCX_HOLDING_TEMPLATE,
        },
        "interface_views": {DEFAULT_HOLDING_INTERFACE_ID: payload},
        "payload": payload,
        "signatories": [owner],
        "observers": [],
        "created_event_blob": f"blob-{contract_id}",
    }


def p2p_registry_contract(contract_id: str, *, registry: str = PARTY) -> dict:
    return {
        "contract_id": contract_id,
        "template_id": {
            "package_id": P2PLENDING_USDCX_PACKAGE_ID,
            "module_name": P2PLENDING_USDCX_MODULE,
            "entity_name": P2PLENDING_USDCX_REGISTRY_TEMPLATE,
        },
        "payload": {
            "registry": registry,
            "observers": [],
        },
        "signatories": [registry],
        "observers": [],
    }


def p2p_created_holding_response(contract_id: str, *, owner: str, amount: str) -> dict:
    contract = p2p_holding_contract(contract_id, owner=owner, amount=amount)
    return {
        "updateId": "update-p2p",
        "recordTime": "2026-07-01T00:00:00Z",
        "events": [
            {
                "createdEvent": {
                    "contractId": contract["contract_id"],
                    "templateId": {
                        "packageId": P2PLENDING_USDCX_PACKAGE_ID,
                        "moduleName": P2PLENDING_USDCX_MODULE,
                        "entityName": P2PLENDING_USDCX_HOLDING_TEMPLATE,
                    },
                    "createArgument": contract["payload"],
                    "interfaceViews": contract["interface_views"],
                    "signatories": contract["signatories"],
                    "observers": contract["observers"],
                    "createdEventBlob": contract["created_event_blob"],
                }
            }
        ],
    }


def zusd_transfer_request(*, amount: str = "1.0000000000", token: dict | None = None) -> TokenTransferRequest:
    return TokenTransferRequest(
        company_id="zalary-demo-001",
        payroll_id="payroll-001",
        employee_external_id="EMP-001",
        salary_claim_contract_id="00claim-zalary-demo-001-payroll-001",
        token=token or ZUSD_TOKEN,
        sender_party=PARTY,
        receiver_party=EMPLOYEE_PARTY,
        amount=amount,
        transfer_reference="SETTLE-payroll-001",
    )


def zusd_holding_candidate(contract_id: str, *, amount: str = "1.0000000000", owner: str = PARTY):
    return ZUSDHoldingCandidate(
        contract_id=contract_id,
        issuer=PARTY,
        owner=owner,
        amount=Decimal(amount),
        symbol="ZUSD",
        reference="faucet",
    )


def zusd_holding_contract(
    contract_id: str,
    *,
    owner: str = PARTY,
    amount: str = "1.0000000000",
    reference: str = "faucet",
    issuer: str = PARTY,
) -> dict:
    payload = {
        "issuer": issuer,
        "owner": owner,
        "amount": amount,
        "symbol": "ZUSD",
        "reference": reference,
        "observers": [],
    }
    return {
        "contract_id": contract_id,
        "template_id": {
            "package_id": "pkg-zalary-test",
            "module_name": ZUSD_HOLDING.module_name,
            "entity_name": ZUSD_HOLDING.entity_name,
        },
        "payload": payload,
        "signatories": [issuer],
        "observers": [owner],
    }


def zusd_transfer_response(
    contract_id: str = "00zusd-receiver",
    *,
    owner: str = EMPLOYEE_PARTY,
    amount: str = "1.0000000000",
    reference: str = "SETTLE-payroll-001",
) -> dict:
    contract = zusd_holding_contract(contract_id, owner=owner, amount=amount, reference=reference)
    return {
        "updateId": "update-zusd-transfer",
        "events": [
            {
                "createdEvent": {
                    "contractId": contract["contract_id"],
                    "templateId": {
                        "packageId": "pkg-zalary-test",
                        "moduleName": ZUSD_HOLDING.module_name,
                        "entityName": ZUSD_HOLDING.entity_name,
                    },
                    "createArgument": contract["payload"],
                    "signatories": contract["signatories"],
                    "observers": contract["observers"],
                }
            }
        ],
    }


def zusd_mint_response(*, holding_id: str = "00zusd-holding", grant_id: str = "00zusd-grant") -> dict:
    holding = zusd_holding_contract(
        holding_id,
        owner=PARTY,
        amount="5000.0000000000",
        reference="faucet-request-manual-001",
    )
    grant_payload = {
        "issuer": PARTY,
        "recipient": PARTY,
        "amount": "5000.0000000000",
        "symbol": "ZUSD",
        "requestId": "faucet-request-manual-001",
        "reference": "faucet-request-manual-001",
        "environment": "sandbox",
        "holdingCid": holding_id,
        "observers": [],
        "grantedAt": "2026-07-01T00:00:00Z",
    }
    return {
        "updateId": "update-zusd-mint",
        "events": [
            {
                "createdEvent": {
                    "contractId": holding_id,
                    "templateId": {
                        "packageId": "pkg-zalary-test",
                        "moduleName": "Zalary.Sandbox.ZUSD",
                        "entityName": "ZUSDHolding",
                    },
                    "createArgument": holding["payload"],
                }
            },
            {
                "createdEvent": {
                    "contractId": grant_id,
                    "templateId": {
                        "packageId": "pkg-zalary-test",
                        "moduleName": "Zalary.Sandbox.ZUSD",
                        "entityName": "ZUSDFaucetGrant",
                    },
                    "createArgument": grant_payload,
                }
            },
        ],
    }


class PayloadBuilderTests(TestCase):
    def test_create_company_choice_payload_normalizes_exact_fields(self):
        payload = create_company_choice_payload(
            companyAdmin=f" {PARTY} ",
            companyName=" Zalary Demo Company ",
            companyId=" zalary-demo-001 ",
            adminWallets=[PARTY],
            hrWallets=[PARTY],
            employerWallets=[PARTY],
            allowedTokens=[TOKEN],
        )

        self.assertEqual(
            payload,
            {
                "companyAdmin": PARTY,
                "companyName": "Zalary Demo Company",
                "companyId": "zalary-demo-001",
                "adminWallets": [PARTY],
                "hrWallets": [PARTY],
                "employerWallets": [PARTY],
                "allowedTokens": [TOKEN],
            },
        )

    def test_company_admin_must_be_in_admin_wallets(self):
        with self.assertRaisesRegex(ValueError, "companyAdmin must be included"):
            create_company_choice_payload(
                companyAdmin=PARTY,
                companyName="Zalary Demo Company",
                companyId="zalary-demo-001",
                adminWallets=[OTHER_PARTY],
                hrWallets=[PARTY],
                employerWallets=[PARTY],
                allowedTokens=[TOKEN],
            )

    def test_party_lists_are_deduplicated(self):
        payload = create_company_choice_payload(
            companyAdmin=PARTY,
            companyName="Zalary Demo Company",
            companyId="zalary-demo-001",
            adminWallets=[PARTY, PARTY],
            hrWallets=[PARTY, PARTY],
            employerWallets=[PARTY, PARTY],
            allowedTokens=[TOKEN],
        )

        self.assertEqual(payload["adminWallets"], [PARTY])
        self.assertEqual(payload["hrWallets"], [PARTY])
        self.assertEqual(payload["employerWallets"], [PARTY])

    def test_create_employee_enrollment_choice_payload_normalizes_exact_fields(self):
        payload = create_employee_enrollment_choice_payload(
            hrWallet=f" {PARTY} ",
            employerWallet=f" {OTHER_PARTY} ",
            employeeWallet=f" {EMPLOYEE_PARTY} ",
            employeeExternalId=" EMP-001 ",
        )

        self.assertEqual(
            payload,
            {
                "hrWallet": PARTY,
                "employerWallet": OTHER_PARTY,
                "employeeWallet": EMPLOYEE_PARTY,
                "employeeExternalId": "EMP-001",
            },
        )

    def test_create_payroll_vault_choice_payload_normalizes_exact_fields(self):
        payload = create_payroll_vault_choice_payload(
            hrWallet=f" {PARTY} ",
            employerWallet=f" {OTHER_PARTY} ",
            payrollId=" payroll-001 ",
            payrollPeriod={"label": "June 2026", "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
            payrollToken=TOKEN,
            claimWindowStart="2026-07-01T00:00:00Z",
            claimWindowEnd="2026-07-31T00:00:00Z",
            expectedEmployeeCount=1,
        )

        self.assertEqual(payload["hrWallet"], PARTY)
        self.assertEqual(payload["employerWallet"], OTHER_PARTY)
        self.assertEqual(payload["payrollId"], "payroll-001")
        self.assertEqual(payload["payrollToken"], TOKEN)
        self.assertEqual(payload["expectedEmployeeCount"], "1")

    def test_add_salary_allocation_choice_payload_normalizes_exact_fields(self):
        payload = add_salary_allocation_choice_payload(
            allocationEmployeeWallet=f" {EMPLOYEE_PARTY} ",
            employeeExternalId=" EMP-001 ",
            salaryBreakdown={
                "grossPay": "1000",
                "allowances": "200",
                "deductions": "100",
                "netPay": "1100",
                "token": TOKEN,
            },
            enrollmentCid=" 00enrollment ",
        )

        self.assertEqual(
            payload,
            {
                "allocationEmployeeWallet": EMPLOYEE_PARTY,
                "employeeExternalId": "EMP-001",
                "salaryBreakdown": salary_payload(),
                "enrollmentCid": "00enrollment",
            },
        )

    def test_confirm_funding_choice_payload_normalizes_exact_fields(self):
        payload = confirm_funding_choice_payload(
            fundingAmount="1100",
            fundingReference=" FUND-payroll-001 ",
            fundingProof=None,
        )

        self.assertEqual(
            payload,
            {
                "fundingAmount": "1100.0000000000",
                "fundingReference": "FUND-payroll-001",
                "fundingProof": None,
            },
        )

    def test_optional_funding_proof_null_encoding(self):
        payload = confirm_funding_choice_payload(
            fundingAmount="1100.00",
            fundingReference="FUND-payroll-001",
        )

        self.assertIsNone(payload["fundingProof"])

    def test_activate_and_issue_claim_ticket_payloads(self):
        self.assertEqual(activate_payroll_choice_payload(), {})
        self.assertEqual(
            issue_claim_ticket_choice_payload(payrollVaultCid=" 00vault "),
            {"payrollVaultCid": "00vault"},
        )

    def test_salary_claim_and_settlement_payloads(self):
        proof = {
            "token": TOKEN,
            "sender": PARTY,
            "receiver": EMPLOYEE_PARTY,
            "amount": "1100",
            "transferReference": "SETTLE-payroll-001",
            "transferInstructionCid": "",
            "holdingCid": None,
            "executedAt": "2026-07-01T00:00:00Z",
        }

        self.assertEqual(request_salary_claim_choice_payload(), {})
        payload = confirm_salary_settlement_choice_payload(
            payrollVaultCid=" 00vault ",
            settlementReference=" SETTLE-payroll-001 ",
            settlementProof=proof,
        )

        self.assertEqual(payload["payrollVaultCid"], "00vault")
        self.assertEqual(payload["settlementReference"], "SETTLE-payroll-001")
        self.assertEqual(payload["settlementProof"]["amount"], "1100.0000000000")
        self.assertIsNone(payload["settlementProof"]["transferInstructionCid"])


class CreateCompanyCommandTests(TestCase):
    def setUp(self):
        self.config = create_platform_config()

    @patch("apps.zalary.services.commands.load_ledger_auth_settings")
    @patch("apps.zalary.services.commands.LedgerClient")
    def test_defaults_token_and_dedupes_act_as(self, ledger_client_cls, load_settings):
        ledger_client = ledger_client_cls.return_value
        ledger_client.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-1",
            status="succeeded",
            raw_response={"updateId": "update-1"},
        )

        result = create_company(
            company_name="Zalary Demo Company",
            company_id="zalary-demo-001",
            sync_after=False,
        )

        command = LedgerCommand.objects.get(command_id=result.command_id)
        call_kwargs = ledger_client.submit_exercise.call_args.kwargs
        self.assertEqual(result.act_as, [PARTY])
        self.assertEqual(call_kwargs["context"].act_as, [PARTY])
        self.assertEqual(call_kwargs["argument"]["allowedTokens"], [TOKEN])
        self.assertEqual(command.payload["allowedTokens"], [TOKEN])
        self.assertEqual(command.status, CommandStatus.SUCCEEDED)
        self.assertEqual(command.update_id, "update-1")

    @patch("apps.zalary.services.commands.load_ledger_auth_settings")
    @patch("apps.zalary.services.commands.LedgerClient")
    def test_failed_submission_marks_command_failed(self, ledger_client_cls, load_settings):
        ledger_client_cls.return_value.submit_exercise.side_effect = LedgerSubmissionError(
            "Ledger API exercise command submission failed with HTTP 400."
        )

        with self.assertRaises(LedgerSubmissionError):
            create_company(
                company_name="Zalary Demo Company",
                company_id="zalary-demo-fail",
                sync_after=False,
            )

        command = LedgerCommand.objects.get(payload__companyId="zalary-demo-fail")
        self.assertEqual(command.status, CommandStatus.FAILED)
        self.assertIn("HTTP 400", command.error_message)

    @patch("apps.zalary.services.commands.LedgerClient")
    def test_duplicate_company_guard_prevents_submission(self, ledger_client_cls):
        create_company_mirror(company_id="zalary-demo-001")

        with self.assertRaises(DuplicateCompanyError):
            create_company(
                company_name="Zalary Demo Company",
                company_id="zalary-demo-001",
                sync_after=False,
            )

        ledger_client_cls.assert_not_called()
        self.assertFalse(LedgerCommand.objects.exists())

    def test_allow_existing_returns_existing_summary(self):
        company = create_company_mirror(company_id="zalary-demo-001")

        result = create_company(
            company_name="Zalary Demo Company",
            company_id="zalary-demo-001",
            sync_after=False,
            allow_existing=True,
        )

        self.assertEqual(result.status, "exists")
        self.assertEqual(result.existing_company["contract_id"], company.contract_id)
        self.assertFalse(LedgerCommand.objects.exists())


class LedgerPartyRegistryTests(TestCase):
    def test_register_ledger_party(self):
        party = register_ledger_party(
            party_id=f" {PARTY} ",
            role=LedgerRole.PLATFORM_ADMIN,
            display_name="Platform Admin",
        )

        self.assertEqual(party.party_id, PARTY)
        self.assertEqual(party.role, LedgerRole.PLATFORM_ADMIN)
        self.assertEqual(party.display_name, "Platform Admin")
        self.assertEqual(LedgerParty.objects.count(), 1)


class EmployeeEnrollmentPreflightTests(TestCase):
    def setUp(self):
        self.company = create_company_mirror(company_id="zalary-demo-001")

    def test_preflight_success(self):
        result = preflight_employee_enrollment(
            company_id=self.company.company_id,
            hr_wallet=PARTY,
            employer_wallet=PARTY,
            employee_wallet=EMPLOYEE_PARTY,
            employee_external_id="EMP-001",
        )

        summary = result.safe_summary()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["act_as"], [PARTY])
        self.assertEqual(summary["future_command"]["choice"], "CreateEmployeeEnrollment")
        self.assertEqual(summary["future_command"]["contract_id"], self.company.contract_id)

    def test_preflight_fails_for_unauthorized_hr(self):
        with self.assertRaisesRegex(OnboardingValidationError, "hrWallet is not authorized"):
            preflight_employee_enrollment(
                company_id=self.company.company_id,
                hr_wallet=OTHER_PARTY,
                employer_wallet=PARTY,
                employee_wallet=EMPLOYEE_PARTY,
                employee_external_id="EMP-001",
            )

    def test_preflight_fails_for_unauthorized_employer(self):
        with self.assertRaisesRegex(OnboardingValidationError, "employerWallet is not authorized"):
            preflight_employee_enrollment(
                company_id=self.company.company_id,
                hr_wallet=PARTY,
                employer_wallet=OTHER_PARTY,
                employee_wallet=EMPLOYEE_PARTY,
                employee_external_id="EMP-001",
            )

    def test_preflight_fails_for_duplicate_employee_external_id(self):
        EmployeeEnrollmentMirror.objects.create(
            contract_id="00enrollment",
            company_id=self.company.company_id,
            company_admin_party=PARTY,
            hr_wallet_party=PARTY,
            employer_wallet_party=PARTY,
            employee_wallet_party=EMPLOYEE_PARTY,
            employee_external_id="EMP-001",
            is_active=True,
        )

        with self.assertRaisesRegex(OnboardingValidationError, "already exists"):
            preflight_employee_enrollment(
                company_id=self.company.company_id,
                hr_wallet=PARTY,
                employer_wallet=PARTY,
                employee_wallet=EMPLOYEE_PARTY,
                employee_external_id="EMP-001",
            )


class CreateEmployeeEnrollmentCommandTests(TestCase):
    def setUp(self):
        self.company = create_company_mirror(company_id="zalary-demo-001")

    @patch("apps.zalary.services.enrollment.load_ledger_auth_settings")
    @patch("apps.zalary.services.enrollment.LedgerClient")
    def test_preflight_called_before_submission_and_command_shape(self, ledger_client_cls, load_settings):
        events = []
        actual_preflight = enrollment_service.preflight_employee_enrollment

        def wrapped_preflight(**kwargs):
            events.append("preflight")
            return actual_preflight(**kwargs)

        def submit_side_effect(**kwargs):
            events.append("submit")
            return LedgerCommandResult(
                command_id=kwargs["context"].command_id,
                update_id="update-enrollment-1",
                status="succeeded",
                raw_response={"updateId": "update-enrollment-1"},
            )

        ledger_client = ledger_client_cls.return_value
        ledger_client.submit_exercise.side_effect = submit_side_effect

        with patch(
            "apps.zalary.services.enrollment.preflight_employee_enrollment",
            side_effect=wrapped_preflight,
        ) as preflight_mock:
            result = create_employee_enrollment(
                company_id=self.company.company_id,
                hr_wallet=PARTY,
                employer_wallet=PARTY,
                employee_wallet=EMPLOYEE_PARTY,
                employee_external_id="EMP-001",
                sync_after=False,
            )

        self.assertEqual(events, ["preflight", "submit"])
        preflight_mock.assert_called_once()
        call_kwargs = ledger_client.submit_exercise.call_args.kwargs
        self.assertEqual(call_kwargs["context"].act_as, [PARTY])
        self.assertEqual(call_kwargs["template"], COMPANY)
        self.assertEqual(call_kwargs["contract_id"], self.company.contract_id)
        self.assertEqual(call_kwargs["choice"], "CreateEmployeeEnrollment")
        self.assertEqual(
            call_kwargs["argument"],
            {
                "hrWallet": PARTY,
                "employerWallet": PARTY,
                "employeeWallet": EMPLOYEE_PARTY,
                "employeeExternalId": "EMP-001",
            },
        )
        self.assertEqual(result.update_id, "update-enrollment-1")
        command = LedgerCommand.objects.get(command_id=result.command_id)
        self.assertEqual(command.contract_id, self.company.contract_id)
        self.assertEqual(command.choice_name, "CreateEmployeeEnrollment")
        self.assertEqual(command.status, CommandStatus.SUCCEEDED)

    @patch("apps.zalary.services.enrollment.LedgerClient")
    def test_duplicate_enrollment_guard_prevents_submission(self, ledger_client_cls):
        EmployeeEnrollmentMirror.objects.create(
            contract_id="00enrollment",
            company_id=self.company.company_id,
            company_admin_party=PARTY,
            hr_wallet_party=PARTY,
            employer_wallet_party=PARTY,
            employee_wallet_party=EMPLOYEE_PARTY,
            employee_external_id="EMP-001",
            is_active=True,
        )

        with self.assertRaises(DuplicateEnrollmentError):
            create_employee_enrollment(
                company_id=self.company.company_id,
                hr_wallet=PARTY,
                employer_wallet=PARTY,
                employee_wallet=EMPLOYEE_PARTY,
                employee_external_id="EMP-001",
                sync_after=False,
            )

        ledger_client_cls.assert_not_called()
        self.assertFalse(LedgerCommand.objects.exists())

    @patch("apps.zalary.services.enrollment.LedgerClient")
    def test_allow_existing_returns_existing_enrollment(self, ledger_client_cls):
        enrollment = EmployeeEnrollmentMirror.objects.create(
            contract_id="00enrollment",
            company_id=self.company.company_id,
            company_admin_party=PARTY,
            hr_wallet_party=PARTY,
            employer_wallet_party=PARTY,
            employee_wallet_party=EMPLOYEE_PARTY,
            employee_external_id="EMP-001",
            is_active=True,
        )

        result = create_employee_enrollment(
            company_id=self.company.company_id,
            hr_wallet=PARTY,
            employer_wallet=PARTY,
            employee_wallet=EMPLOYEE_PARTY,
            employee_external_id="EMP-001",
            sync_after=False,
            allow_existing=True,
        )

        self.assertEqual(result.status, "exists")
        self.assertEqual(result.existing_enrollment["contract_id"], enrollment.contract_id)
        ledger_client_cls.assert_not_called()
        self.assertFalse(LedgerCommand.objects.exists())


class EmployeeEnrollmentSyncTests(TestCase):
    @patch("apps.zalary.services.sync.load_ledger_auth_settings")
    @patch("apps.zalary.services.sync.LedgerClient")
    def test_sync_employee_enrollments_upserts_mirror_rows(self, ledger_client_cls, load_settings):
        ledger_client_cls.return_value.query_active_contracts.return_value = [
            {
                "contract_id": "00enrollment",
                "template_id": {
                    "package_id": "pkg",
                    "module_name": "Zalary.Enrollment",
                    "entity_name": "EmployeeEnrollment",
                },
                "payload": {
                    "companyId": "zalary-demo-001",
                    "companyAdmin": PARTY,
                    "hrWallet": PARTY,
                    "employerWallet": PARTY,
                    "employeeWallet": EMPLOYEE_PARTY,
                    "employeeExternalId": "EMP-001",
                    "isActive": True,
                    "walletAssigned": True,
                },
                "ledger_offset": "42",
                "signatories": [PARTY],
                "observers": [EMPLOYEE_PARTY],
            }
        ]

        result = sync_employee_enrollments(company_id="zalary-demo-001", parties=[PARTY])

        self.assertEqual(result.synced_count, 1)
        mirror = EmployeeEnrollmentMirror.objects.get(contract_id="00enrollment")
        self.assertEqual(mirror.company_id, "zalary-demo-001")
        self.assertEqual(mirror.employee_external_id, "EMP-001")
        self.assertTrue(mirror.is_active)


class PayrollPreflightTests(TestCase):
    def setUp(self):
        self.company = create_company_mirror(company_id="zalary-demo-001")
        self.enrollment = create_enrollment_mirror(company_id=self.company.company_id)

    def test_payroll_vault_preflight_success(self):
        result = preflight_payroll_vault_creation(
            company_id=self.company.company_id,
            hr_wallet=PARTY,
            employer_wallet=PARTY,
            payroll_id="payroll-001",
            payroll_period={"label": "June 2026", "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
            payroll_token=TOKEN,
            claim_window_start="2026-07-01T00:00:00Z",
            claim_window_end="2026-07-31T00:00:00Z",
            expected_employee_count=1,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.act_as, [PARTY])
        self.assertEqual(result.future_command["choice"], "CreatePayrollVault")
        self.assertEqual(result.future_command["contract_id"], self.company.contract_id)

    def test_payroll_vault_preflight_fails_for_unauthorized_hr(self):
        with self.assertRaisesRegex(OnboardingValidationError, "hrWallet is not authorized"):
            preflight_payroll_vault_creation(
                company_id=self.company.company_id,
                hr_wallet=OTHER_PARTY,
                employer_wallet=PARTY,
                payroll_id="payroll-001",
                payroll_period={"label": "June 2026", "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
                payroll_token=TOKEN,
                claim_window_start="2026-07-01T00:00:00Z",
                claim_window_end="2026-07-31T00:00:00Z",
                expected_employee_count=1,
            )

    def test_payroll_vault_preflight_fails_for_unauthorized_employer(self):
        with self.assertRaisesRegex(OnboardingValidationError, "employerWallet is not authorized"):
            preflight_payroll_vault_creation(
                company_id=self.company.company_id,
                hr_wallet=PARTY,
                employer_wallet=OTHER_PARTY,
                payroll_id="payroll-001",
                payroll_period={"label": "June 2026", "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
                payroll_token=TOKEN,
                claim_window_start="2026-07-01T00:00:00Z",
                claim_window_end="2026-07-31T00:00:00Z",
                expected_employee_count=1,
            )

    def test_duplicate_payroll_id_blocked(self):
        create_payroll_vault_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        with self.assertRaises(DuplicatePayrollVaultError):
            preflight_payroll_vault_creation(
                company_id=self.company.company_id,
                hr_wallet=PARTY,
                employer_wallet=PARTY,
                payroll_id="payroll-001",
                payroll_period={"label": "June 2026", "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
                payroll_token=TOKEN,
                claim_window_start="2026-07-01T00:00:00Z",
                claim_window_end="2026-07-31T00:00:00Z",
                expected_employee_count=1,
            )

    def test_duplicate_allocation_blocked(self):
        create_payroll_vault_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        create_salary_allocation_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        with self.assertRaises(DuplicateSalaryAllocationError):
            preflight_salary_allocation(
                company_id=self.company.company_id,
                payroll_id="payroll-001",
                allocation_employee_wallet=EMPLOYEE_PARTY,
                employee_external_id="EMP-001",
                salary_breakdown=salary_payload(),
                enrollment_cid=self.enrollment.contract_id,
            )

    def test_finalize_blocked_before_expected_allocation_count(self):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            uploaded_count=0,
            expected_count=1,
            total_net_pay="0.0000000000",
        )

        with self.assertRaisesRegex(OnboardingValidationError, "must equal expected employee count"):
            preflight_finalize_allocations(company_id=self.company.company_id, payroll_id="payroll-001")


class PayrollCommandTests(TestCase):
    def setUp(self):
        self.company = create_company_mirror(company_id="zalary-demo-001")

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_create_payroll_vault_command_act_as_is_hr_wallet(self, ledger_client_cls, load_settings):
        ledger_client = ledger_client_cls.return_value
        ledger_client.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-payroll-1",
            status="succeeded",
            raw_response={"updateId": "update-payroll-1"},
        )

        result = create_payroll_vault(
            company_id=self.company.company_id,
            hr_wallet=PARTY,
            employer_wallet=PARTY,
            payroll_id="payroll-001",
            payroll_period={"label": "June 2026", "startsAt": "2026-06-01", "endsAt": "2026-06-30"},
            payroll_token=TOKEN,
            claim_window_start="2026-07-01T00:00:00Z",
            claim_window_end="2026-07-31T00:00:00Z",
            expected_employee_count=1,
            sync_after=False,
        )

        call_kwargs = ledger_client.submit_exercise.call_args.kwargs
        self.assertEqual(call_kwargs["context"].act_as, [PARTY])
        self.assertEqual(call_kwargs["choice"], "CreatePayrollVault")
        self.assertEqual(call_kwargs["contract_id"], self.company.contract_id)
        self.assertEqual(result.update_id, "update-payroll-1")
        command = LedgerCommand.objects.get(command_id=result.command_id)
        self.assertEqual(command.status, CommandStatus.SUCCEEDED)
        self.assertEqual(command.act_as, [PARTY])

    def test_demo_pipeline_stops_on_failed_step(self):
        create_enrollment_mirror(company_id=self.company.company_id)
        create_payroll_vault_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        create_step = payroll_service.PayrollCommandStepResult(
            status="ok",
            action="CreatePayrollVault",
            command_id="cmd-1",
            update_id="update-1",
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            ledger_command_pk=1,
            contract_id="00vault",
        )

        with patch("apps.zalary.services.payroll.create_payroll_vault", return_value=create_step):
            with patch(
                "apps.zalary.services.payroll.add_salary_allocation",
                side_effect=OnboardingValidationError("allocation failed"),
            ):
                with patch("apps.zalary.services.payroll.finalize_allocations") as finalize_mock:
                    with self.assertRaisesRegex(OnboardingValidationError, "allocation failed"):
                        create_demo_payroll_pipeline(
                            company_id=self.company.company_id,
                            employee_external_id="EMP-001",
                            payroll_id="payroll-001",
                        )

        finalize_mock.assert_not_called()

    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_demo_pipeline_allow_existing_does_not_create_duplicate_commands(self, ledger_client_cls):
        create_enrollment_mirror(company_id=self.company.company_id)
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="AllocationsFinalized",
            uploaded_count=1,
            expected_count=1,
            total_net_pay="1100.0000000000",
        )
        create_salary_allocation_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        result = create_demo_payroll_pipeline(
            company_id=self.company.company_id,
            employee_external_id="EMP-001",
            payroll_id="payroll-001",
            allow_existing=True,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual([step.status for step in result.steps], ["exists", "exists", "exists"])
        ledger_client_cls.assert_not_called()
        self.assertFalse(LedgerCommand.objects.exists())


class FundingActivationClaimTicketTests(TestCase):
    def setUp(self):
        self.company = create_company_mirror(company_id="zalary-demo-001")

    def test_confirm_funding_preflight_success(self):
        vault = create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="AllocationsFinalized",
            uploaded_count=1,
            expected_count=1,
            total_net_pay="1100.0000000000",
        )

        result = preflight_confirm_funding(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            funding_amount="1100",
            funding_reference="FUND-payroll-001",
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.act_as, [PARTY])
        self.assertEqual(result.payroll_vault_contract_id, vault.contract_id)

    def test_confirm_funding_fails_if_vault_not_finalized(self):
        create_payroll_vault_mirror(company_id=self.company.company_id, payroll_id="payroll-001", status="Created")

        with self.assertRaisesRegex(OnboardingValidationError, "AllocationsFinalized"):
            preflight_confirm_funding(
                company_id=self.company.company_id,
                payroll_id="payroll-001",
                funding_amount="1100",
                funding_reference="FUND-payroll-001",
            )

    def test_funding_amount_must_cover_total_net_pay(self):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="AllocationsFinalized",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )

        with self.assertRaisesRegex(OnboardingValidationError, "cover totalNetPay"):
            preflight_confirm_funding(
                company_id=self.company.company_id,
                payroll_id="payroll-001",
                funding_amount="1000",
                funding_reference="FUND-payroll-001",
            )

    def test_activate_payroll_preflight_success(self):
        vault = create_payroll_vault_mirror(company_id=self.company.company_id, payroll_id="payroll-001", status="Funded")

        result = preflight_activate_payroll(company_id=self.company.company_id, payroll_id="payroll-001")

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.act_as, [PARTY])
        self.assertEqual(result.payroll_vault_contract_id, vault.contract_id)

    def test_activate_payroll_fails_if_not_funded(self):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="AllocationsFinalized",
        )

        with self.assertRaisesRegex(OnboardingValidationError, "Funded"):
            preflight_activate_payroll(company_id=self.company.company_id, payroll_id="payroll-001")

    def test_issue_claim_ticket_preflight_success_when_window_open(self):
        vault = create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        allocation = create_salary_allocation_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        result = preflight_issue_claim_ticket(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.act_as, [PARTY])
        self.assertEqual(result.payroll_vault_contract_id, vault.contract_id)
        self.assertEqual(result.salary_allocation_contract_id, allocation.contract_id)

    def test_issue_claim_ticket_returns_pending_when_window_not_open(self):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
            claim_window_start=timezone.now() + timedelta(days=1),
            claim_window_end=timezone.now() + timedelta(days=30),
        )
        create_salary_allocation_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        result = preflight_issue_claim_ticket(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
        )

        self.assertEqual(result.status, "pending_claim_window_open")
        self.assertIn("not opened", result.reason)

    def test_duplicate_claim_ticket_guard(self):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        create_salary_allocation_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        create_claim_ticket_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        with self.assertRaises(DuplicateClaimTicketError):
            preflight_issue_claim_ticket(
                company_id=self.company.company_id,
                payroll_id="payroll-001",
                employee_external_id="EMP-001",
            )

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_confirm_funding_command_act_as_is_employer_wallet(self, ledger_client_cls, load_settings):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="AllocationsFinalized",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-funding-1",
            status="succeeded",
            raw_response={"updateId": "update-funding-1"},
        )

        result = confirm_funding(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            funding_amount="1100",
            funding_reference="FUND-payroll-001",
            sync_after=False,
        )

        call_kwargs = ledger_client_cls.return_value.submit_exercise.call_args.kwargs
        self.assertEqual(call_kwargs["context"].act_as, [PARTY])
        self.assertEqual(call_kwargs["choice"], "ConfirmFunding")
        self.assertEqual(result.update_id, "update-funding-1")

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_activate_payroll_command_act_as_is_employer_wallet(self, ledger_client_cls, load_settings):
        create_payroll_vault_mirror(company_id=self.company.company_id, payroll_id="payroll-001", status="Funded")
        ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-activate-1",
            status="succeeded",
            raw_response={"updateId": "update-activate-1"},
        )

        result = activate_payroll(company_id=self.company.company_id, payroll_id="payroll-001", sync_after=False)

        call_kwargs = ledger_client_cls.return_value.submit_exercise.call_args.kwargs
        self.assertEqual(call_kwargs["context"].act_as, [PARTY])
        self.assertEqual(call_kwargs["choice"], "ActivatePayroll")
        self.assertEqual(result.update_id, "update-activate-1")

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_issue_claim_ticket_command_act_as_is_hr_wallet(self, ledger_client_cls, load_settings):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        create_salary_allocation_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-ticket-1",
            status="succeeded",
            raw_response={"updateId": "update-ticket-1"},
        )

        result = issue_claim_ticket(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
            sync_after=False,
        )

        call_kwargs = ledger_client_cls.return_value.submit_exercise.call_args.kwargs
        self.assertEqual(call_kwargs["context"].act_as, [PARTY])
        self.assertEqual(call_kwargs["choice"], "IssueClaimTicket")
        self.assertEqual(result.update_id, "update-ticket-1")

    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_funding_ticket_pipeline_allow_existing_does_not_submit_duplicates(self, ledger_client_cls):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        create_salary_allocation_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        create_claim_ticket_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        result = create_demo_funding_activation_ticket_pipeline(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
            allow_existing=True,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual([step.status for step in result.steps], ["exists", "exists", "exists"])
        ledger_client_cls.assert_not_called()
        self.assertFalse(LedgerCommand.objects.exists())


class SalaryClaimSettlementTests(TestCase):
    def setUp(self):
        self.company = create_company_mirror(company_id="zalary-demo-001")

    def _active_payroll_inputs(self):
        create_payroll_vault_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        create_salary_allocation_mirror(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            status="AllocationClaimTicketIssued",
        )
        create_claim_ticket_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

    def test_request_salary_claim_preflight_uses_employee_wallet(self):
        self._active_payroll_inputs()

        result = preflight_request_salary_claim(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.act_as, [EMPLOYEE_PARTY])
        self.assertEqual(result.future_command["choice"], "RequestSalaryClaim")

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_request_salary_claim_command_act_as_is_employee_wallet(self, ledger_client_cls, load_settings):
        self._active_payroll_inputs()
        ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-claim-1",
            status="succeeded",
            raw_response={"updateId": "update-claim-1"},
        )

        result = request_salary_claim(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
            sync_after=False,
        )

        call_kwargs = ledger_client_cls.return_value.submit_exercise.call_args.kwargs
        self.assertEqual(call_kwargs["context"].act_as, [EMPLOYEE_PARTY])
        self.assertEqual(call_kwargs["choice"], "RequestSalaryClaim")
        self.assertEqual(result.update_id, "update-claim-1")
        command = LedgerCommand.objects.get(command_id=result.command_id)
        self.assertEqual(command.status, CommandStatus.SUCCEEDED)

    def test_duplicate_salary_claim_blocked(self):
        self._active_payroll_inputs()
        create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        with self.assertRaises(DuplicateSalaryClaimError):
            request_salary_claim(
                company_id=self.company.company_id,
                payroll_id="payroll-001",
                employee_external_id="EMP-001",
                sync_after=False,
            )

    def test_validate_settlement_proof_rejects_reference_mismatch(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        proof = demo_settlement_proof(claim=claim, settlement_reference="SETTLE-payroll-001")
        proof["transferReference"] = "DIFFERENT"

        with self.assertRaisesRegex(SettlementProofError, "transferReference"):
            validate_settlement_proof(
                claim=claim,
                settlement_reference="SETTLE-payroll-001",
                settlement_proof=proof,
            )

    def test_validate_settlement_proof_rejects_token_mismatch(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        proof = demo_settlement_proof(claim=claim, settlement_reference="SETTLE-payroll-001")
        proof["token"] = {**TOKEN, "instrumentId": "OTHER"}

        with self.assertRaisesRegex(SettlementProofError, "token"):
            validate_settlement_proof(
                claim=claim,
                settlement_reference="SETTLE-payroll-001",
                settlement_proof=proof,
            )

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_confirm_salary_settlement_command_act_as_is_employer_wallet(self, ledger_client_cls, load_settings):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-settlement-1",
            status="succeeded",
            raw_response={"updateId": "update-settlement-1"},
        )

        with patch.dict(os.environ, {"ZALARY_ENABLE_DEMO_SETTLEMENT_PROOF": "true"}):
            result = confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                demo_proof=True,
                sync_after=False,
            )

        call_kwargs = ledger_client_cls.return_value.submit_exercise.call_args.kwargs
        self.assertEqual(call_kwargs["context"].act_as, [PARTY])
        self.assertEqual(call_kwargs["choice"], "ConfirmSalarySettlement")
        self.assertEqual(call_kwargs["argument"]["settlementProof"]["sender"], PARTY)
        self.assertEqual(call_kwargs["argument"]["settlementProof"]["receiver"], EMPLOYEE_PARTY)
        self.assertEqual(result.update_id, "update-settlement-1")
        transfer = USDCxTransferRecord.objects.get()
        self.assertEqual(transfer.provider_name, "demo")
        self.assertEqual(transfer.provider_status, "completed")
        self.assertEqual(transfer.ledger_command_id, result.ledger_command_pk)

    def test_demo_proof_rejected_by_default(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        with self.assertRaisesRegex(SettlementProofError, "demoProof is disabled"):
            confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                demo_proof=True,
                sync_after=False,
            )

        self.assertFalse(LedgerCommand.objects.exists())
        self.assertFalse(USDCxTransferRecord.objects.exists())

    def test_production_settlement_never_invents_proof_without_provider(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        with self.assertRaisesRegex(SettlementProofError, "No token transfer provider"):
            confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                sync_after=False,
            )

        self.assertFalse(LedgerCommand.objects.exists())
        transfer = USDCxTransferRecord.objects.get()
        self.assertEqual(transfer.provider_status, TRANSFER_UNAVAILABLE)

    def test_external_proof_rejected_by_default(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        proof = demo_settlement_proof(claim=claim, settlement_reference="SETTLE-payroll-001")

        with self.assertRaisesRegex(SettlementProofError, "External settlementProof is disabled"):
            confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                settlement_proof=proof,
                sync_after=False,
            )

        self.assertFalse(LedgerCommand.objects.exists())

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_external_proof_accepted_only_when_enabled(self, ledger_client_cls, load_settings):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        proof = demo_settlement_proof(claim=claim, settlement_reference="SETTLE-payroll-001")
        ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-external-proof",
            status="succeeded",
            raw_response={"updateId": "update-external-proof"},
        )

        with patch.dict(os.environ, {"ZALARY_USDCX_ALLOW_EXTERNAL_PROOF": "true"}):
            result = confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                settlement_proof=proof,
                sync_after=False,
            )

        self.assertEqual(result.status, "ok")
        transfer = USDCxTransferRecord.objects.get()
        self.assertEqual(transfer.provider_name, "external")
        self.assertEqual(transfer.provider_status, "completed")

    def test_confirm_salary_settlement_duplicate_allow_existing(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        receipt = create_settlement_receipt_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        result = confirm_salary_settlement(
            salary_claim_contract_id=claim.contract_id,
            settlement_reference=receipt.settlement_reference,
            demo_proof=True,
            allow_existing=True,
            sync_after=False,
        )

        self.assertEqual(result.status, "exists")
        self.assertEqual(result.contract_id, receipt.contract_id)
        self.assertFalse(LedgerCommand.objects.exists())

    def test_confirm_salary_settlement_duplicate_blocked(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        create_settlement_receipt_mirror(company_id=self.company.company_id, payroll_id="payroll-001")

        with self.assertRaises(DuplicateSettlementError):
            confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                demo_proof=True,
                sync_after=False,
            )

    @patch("apps.zalary.services.settlement.sync_final_payroll_execution_state")
    @patch("apps.zalary.services.settlement.create_demo_funding_activation_ticket_pipeline")
    def test_full_pipeline_allow_existing_does_not_submit_duplicates(self, setup_pipeline, final_sync):
        self._active_payroll_inputs()
        create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        create_settlement_receipt_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        setup_pipeline.return_value = SimpleNamespace(
            status="ok",
            safe_summary=lambda: {"status": "ok", "steps": []},
        )
        final_sync.return_value = {"status": "ok"}

        result = create_demo_full_payroll_execution(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
            settlement_reference="SETTLE-payroll-001",
            allow_existing=True,
            demo_proof=True,
        )

        self.assertEqual(result.salary_claim_step.status, "exists")
        self.assertEqual(result.settlement_step.status, "exists")
        self.assertFalse(LedgerCommand.objects.exists())

    def test_demo_proof_management_command_rejected_by_default(self):
        self._active_payroll_inputs()
        create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        stdout = StringIO()
        with patch("apps.zalary.services.settlement.create_demo_funding_activation_ticket_pipeline") as setup_pipeline:
            setup_pipeline.return_value = SimpleNamespace(
                status="ok",
                safe_summary=lambda: {"status": "ok", "steps": []},
            )
            with self.assertRaises(CommandError):
                call_command(
                    "zalary_create_demo_full_payroll_execution",
                    company_id=self.company.company_id,
                    payroll_id="payroll-001",
                    employee_external_id="EMP-001",
                    demo_proof=True,
                    allow_existing=True,
                    stdout=stdout,
                )

        self.assertIn("demoProof is disabled", stdout.getvalue())

    def test_pending_claim_request_command_returns_pending_without_submit(self):
        self._active_payroll_inputs()
        workflow_id = key_request_salary_claim(
            company_id=self.company.company_id,
            payroll_id="payroll-001",
            employee_external_id="EMP-001",
        )
        LedgerCommand.objects.create(
            command_id="pending-claim-command",
            workflow_id=workflow_id,
            template_id=CLAIM_TICKET.display_id(),
            contract_id="00ticket-zalary-demo-001-payroll-001",
            choice_name="RequestSalaryClaim",
            status=CommandStatus.SUBMITTED,
        )

        with patch("apps.zalary.services.payroll.LedgerClient") as ledger_client_cls:
            result = request_salary_claim(
                company_id=self.company.company_id,
                payroll_id="payroll-001",
                employee_external_id="EMP-001",
                sync_after=False,
            )

        self.assertEqual(result.status, "pending")
        ledger_client_cls.assert_not_called()

    def test_pending_settlement_command_returns_pending_without_transfer(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        workflow_id = key_confirm_settlement(
            salary_claim_contract_id=claim.contract_id,
            settlement_reference="SETTLE-payroll-001",
        )
        LedgerCommand.objects.create(
            command_id="pending-settlement-command",
            workflow_id=workflow_id,
            template_id=SALARY_CLAIM.display_id(),
            contract_id=claim.contract_id,
            choice_name="ConfirmSalarySettlement",
            status=CommandStatus.PENDING,
        )

        result = confirm_salary_settlement(
            salary_claim_contract_id=claim.contract_id,
            settlement_reference="SETTLE-payroll-001",
            sync_after=False,
        )

        self.assertEqual(result.status, "pending")
        self.assertFalse(USDCxTransferRecord.objects.exists())

    def test_provider_pending_returns_pending_without_settlement_submission(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        provider = SimpleNamespace(
            provider_name="fake-provider",
            execute_transfer=lambda request: TokenTransferResult(
                status=TRANSFER_PENDING,
                token=request.token,
                sender=request.sender_party,
                receiver=request.receiver_party,
                amount=request.amount,
                transferReference=request.transfer_reference,
                transferInstructionCid="00instruction-pending",
                provider_name="fake-provider",
            ),
            build_token_transfer_proof=BaseTokenTransferProvider().build_token_transfer_proof,
        )

        with patch("apps.zalary.services.settlement.get_token_transfer_provider", return_value=provider):
            result = confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                sync_after=False,
            )

        self.assertEqual(result.status, "pending")
        self.assertFalse(LedgerCommand.objects.exists())
        transfer = USDCxTransferRecord.objects.get()
        self.assertEqual(transfer.provider_status, TRANSFER_PENDING)
        self.assertEqual(transfer.transfer_instruction_cid, "00instruction-pending")

    def test_provider_failed_and_unavailable_block_settlement(self):
        self._active_payroll_inputs()
        statuses = [TRANSFER_FAILED, TRANSFER_UNAVAILABLE]
        for provider_status in statuses:
            SalaryClaimMirror.objects.all().delete()
            LedgerCommand.objects.all().delete()
            USDCxTransferRecord.objects.all().delete()
            claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
            provider = SimpleNamespace(
                provider_name="fake-provider",
                execute_transfer=lambda request, status=provider_status: TokenTransferResult(
                    status=status,
                    token=request.token,
                    sender=request.sender_party,
                    receiver=request.receiver_party,
                    amount=request.amount,
                    transferReference=request.transfer_reference,
                    provider_name="fake-provider",
                    error_message=f"{status} transfer",
                ),
                build_token_transfer_proof=BaseTokenTransferProvider().build_token_transfer_proof,
            )
            with patch("apps.zalary.services.settlement.get_token_transfer_provider", return_value=provider):
                with self.assertRaisesRegex(SettlementProofError, f"{provider_status} transfer"):
                    confirm_salary_settlement(
                        salary_claim_contract_id=claim.contract_id,
                        settlement_reference="SETTLE-payroll-001",
                        sync_after=False,
                    )
            self.assertFalse(LedgerCommand.objects.exists())

    def test_zusd_failed_provider_does_not_submit_payroll_settlement(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        provider = SimpleNamespace(
            provider_name=ZALARY_TEST_TOKEN_PROVIDER_MODE,
            execute_transfer=lambda request: TokenTransferResult(
                status=TRANSFER_FAILED,
                token=request.token,
                sender=request.sender_party,
                receiver=request.receiver_party,
                amount=request.amount,
                transferReference=request.transfer_reference,
                provider_name=ZALARY_TEST_TOKEN_PROVIDER_MODE,
                error_message="ZUSD transfer failed",
            ),
            build_token_transfer_proof=BaseTokenTransferProvider().build_token_transfer_proof,
        )

        with patch("apps.zalary.services.settlement.get_token_transfer_provider", return_value=provider):
            with self.assertRaisesRegex(SettlementProofError, "ZUSD transfer failed"):
                confirm_salary_settlement(
                    salary_claim_contract_id=claim.contract_id,
                    settlement_reference="SETTLE-payroll-001",
                    sync_after=False,
                )

        self.assertFalse(LedgerCommand.objects.exists())
        transfer = USDCxTransferRecord.objects.get()
        self.assertEqual(transfer.provider_name, ZALARY_TEST_TOKEN_PROVIDER_MODE)
        self.assertEqual(transfer.provider_status, TRANSFER_FAILED)

    @patch("apps.zalary.services.payroll.load_ledger_auth_settings")
    @patch("apps.zalary.services.payroll.LedgerClient")
    def test_completed_provider_result_submits_settlement(self, ledger_client_cls, load_settings):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
            command_id="ignored",
            update_id="update-provider-proof",
            status="succeeded",
            raw_response={"updateId": "update-provider-proof"},
        )
        provider = SimpleNamespace(
            provider_name="fake-provider",
            execute_transfer=lambda request: TokenTransferResult(
                status=TRANSFER_COMPLETED,
                token=request.token,
                sender=request.sender_party,
                receiver=request.receiver_party,
                amount=request.amount,
                transferReference=request.transfer_reference,
                transferInstructionCid="00instruction",
                holdingCid="00holding",
                executedAt=timezone.now().isoformat(),
                provider_name="fake-provider",
                raw_provider_reference="provider-ref-1",
            ),
            build_token_transfer_proof=BaseTokenTransferProvider().build_token_transfer_proof,
        )

        with patch("apps.zalary.services.settlement.get_token_transfer_provider", return_value=provider):
            result = confirm_salary_settlement(
                salary_claim_contract_id=claim.contract_id,
                settlement_reference="SETTLE-payroll-001",
                sync_after=False,
            )

        self.assertEqual(result.status, "ok")
        transfer = USDCxTransferRecord.objects.get()
        self.assertEqual(transfer.provider_name, "fake-provider")
        self.assertEqual(transfer.provider_status, TRANSFER_COMPLETED)
        self.assertEqual(transfer.transfer_instruction_cid, "00instruction")
        self.assertEqual(transfer.ledger_command_id, result.ledger_command_pk)

    def test_provider_never_builds_proof_from_intent_alone(self):
        provider = BaseTokenTransferProvider()
        with self.assertRaisesRegex(SettlementProofError, "completed transfer"):
            provider.build_token_transfer_proof(TokenTransferResult(status=TRANSFER_PENDING, provider_name="fake"))

    def test_claim_ticket_sync_marks_consumed_ticket_inactive(self):
        self._active_payroll_inputs()
        with patch("apps.zalary.services.sync.load_ledger_auth_settings"):
            with patch("apps.zalary.services.sync.LedgerClient") as ledger_client_cls:
                ledger_client_cls.return_value.query_active_contracts.return_value = []
                sync_claim_tickets(
                    company_id=self.company.company_id,
                    payroll_id="payroll-001",
                    employee_external_id="EMP-001",
                )

        self.assertFalse(ClaimTicketMirror.objects.get().ledger_active)

    def test_salary_claim_sync_marks_consumed_claim_archived(self):
        self._active_payroll_inputs()
        claim = create_salary_claim_mirror(company_id=self.company.company_id, payroll_id="payroll-001")
        with patch("apps.zalary.services.sync.load_ledger_auth_settings"):
            with patch("apps.zalary.services.sync.LedgerClient") as ledger_client_cls:
                ledger_client_cls.return_value.query_active_contracts.return_value = []
                sync_salary_claims(
                    company_id=self.company.company_id,
                    payroll_id="payroll-001",
                    employee_external_id="EMP-001",
                )

        claim.refresh_from_db()
        self.assertFalse(claim.ledger_active)
        self.assertEqual(claim.claim_status, "Archived")


class ZUSDFaucetServiceTests(TestCase):
    def test_faucet_success_mints_holding_and_grant(self):
        env = {
            "ZALARY_TEST_TOKEN_ENABLED": "true",
            "ZALARY_TEST_TOKEN_ENVIRONMENT": "sandbox",
            "ZALARY_TEST_TOKEN_ISSUER_PARTY": PARTY,
            "ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID": "00zusd-issuer",
        }
        with patch.dict(os.environ, env):
            with patch("apps.zalary.services.faucet.load_ledger_auth_settings"):
                with patch("apps.zalary.services.faucet.LedgerClient") as ledger_client_cls:
                    ledger_client_cls.return_value.submit_exercise.return_value = LedgerCommandResult(
                        command_id="cmd",
                        update_id="update-zusd-mint",
                        status="succeeded",
                        raw_response=zusd_mint_response(),
                    )
                    result = request_zusd_faucet_mint(
                        owner_party=PARTY,
                        amount="5000.0000000000",
                        reference="faucet-request-manual-001",
                        request_id="faucet-request-manual-001",
                    )

        self.assertEqual(result.status, "minted")
        self.assertEqual(result.holding_contract_id, "00zusd-holding")
        self.assertEqual(result.grant_contract_id, "00zusd-grant")
        request = ZUSDFaucetRequest.objects.get(request_id="faucet-request-manual-001")
        self.assertEqual(request.status, FaucetRequestStatus.MINTED)
        self.assertEqual(request.update_id, "update-zusd-mint")
        self.assertTrue(LedgerCommand.objects.filter(choice_name="MintZUSD").exists())

    def test_faucet_rejects_disabled_mode(self):
        env = {
            "ZALARY_TEST_TOKEN_ENABLED": "false",
            "ZALARY_TEST_TOKEN_ENVIRONMENT": "sandbox",
            "ZALARY_TEST_TOKEN_ISSUER_PARTY": PARTY,
        }
        with patch.dict(os.environ, env):
            with self.assertRaisesRegex(ConfigurationError, "disabled"):
                request_zusd_faucet_mint(
                    owner_party=PARTY,
                    amount="1.0000000000",
                    reference="disabled-request",
                    request_id="disabled-request",
                )

        request = ZUSDFaucetRequest.objects.get(request_id="disabled-request")
        self.assertEqual(request.status, FaucetRequestStatus.REJECTED)
        self.assertFalse(LedgerCommand.objects.exists())

    def test_faucet_rejects_over_max_daily_and_monthly_limits(self):
        base_env = {
            "ZALARY_TEST_TOKEN_ENABLED": "true",
            "ZALARY_TEST_TOKEN_ENVIRONMENT": "sandbox",
            "ZALARY_TEST_TOKEN_ISSUER_PARTY": PARTY,
            "ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID": "00zusd-issuer",
            "ZALARY_TEST_TOKEN_MAX_GRANT_AMOUNT": "10.0000000000",
            "ZALARY_TEST_TOKEN_DAILY_LIMIT": "10.0000000000",
            "ZALARY_TEST_TOKEN_MONTHLY_LIMIT": "20.0000000000",
        }
        scenarios = [
            ("over-max", "11.0000000000", "max grant"),
            ("daily", "2.0000000000", "daily limit"),
            ("monthly", "2.0000000000", "monthly limit"),
        ]
        for request_id, amount, message in scenarios:
            with self.subTest(request_id=request_id):
                ZUSDFaucetRequest.objects.all().delete()
                env = dict(base_env)
                if request_id == "daily":
                    ZUSDFaucetRequest.objects.create(
                        request_id="daily-existing",
                        owner_party=PARTY,
                        issuer_party=PARTY,
                        amount="9.0000000000",
                        reference="daily-existing",
                        environment="sandbox",
                        status=FaucetRequestStatus.MINTED,
                    )
                if request_id == "monthly":
                    env["ZALARY_TEST_TOKEN_DAILY_LIMIT"] = "100.0000000000"
                    ZUSDFaucetRequest.objects.create(
                        request_id="monthly-existing",
                        owner_party=PARTY,
                        issuer_party=PARTY,
                        amount="19.0000000000",
                        reference="monthly-existing",
                        environment="sandbox",
                        status=FaucetRequestStatus.MINTED,
                    )
                with patch.dict(os.environ, env):
                    with self.assertRaisesRegex(ConfigurationError, message):
                        request_zusd_faucet_mint(
                            owner_party=PARTY,
                            amount=amount,
                            reference=request_id,
                            request_id=request_id,
                        )
                self.assertEqual(ZUSDFaucetRequest.objects.get(request_id=request_id).status, FaucetRequestStatus.REJECTED)

    def test_balance_sums_visible_holdings(self):
        holdings = [
            zusd_holding_candidate("00zusd-1", amount="2.0000000000"),
            zusd_holding_candidate("00zusd-2", amount="3.0000000000"),
        ]
        with patch.dict(os.environ, {"ZALARY_TEST_TOKEN_ENVIRONMENT": "sandbox"}):
            with patch.object(ConfiguredZUSDTransferProvider, "list_zusd_holdings", return_value=holdings):
                result = get_zusd_balance(owner_party=PARTY)

        self.assertEqual(result.balance, "5.0000000000")
        self.assertEqual(result.holding_count, 2)


class ZUSDSandboxTokenProviderTests(TestCase):
    def setUp(self):
        self.provider = ConfiguredZUSDTransferProvider(require_explicit_provider=False)
        self.request = zusd_transfer_request()

    def test_provider_selection_separates_zusd_and_usdcx(self):
        with patch.dict(os.environ, {"ZALARY_TOKEN_TRANSFER_PROVIDER": ZALARY_TEST_TOKEN_PROVIDER_MODE}):
            zusd_provider = get_token_transfer_provider()
        with patch.dict(
            os.environ,
            {"ZALARY_TOKEN_TRANSFER_PROVIDER": "usdcx", "ZALARY_USDCX_TRANSFER_PROVIDER": "token_standard"},
        ):
            usdcx_provider = get_token_transfer_provider()

        self.assertIsInstance(zusd_provider, ConfiguredZUSDTransferProvider)
        self.assertIsInstance(usdcx_provider, ConfiguredUSDCxTransferProvider)

    def test_select_holding_supports_exact_and_partial_transfer(self):
        holdings = [
            zusd_holding_candidate("00zusd-5", amount="5.0000000000"),
            zusd_holding_candidate("00zusd-1", amount="1.0000000000"),
        ]

        exact = select_zusd_holding(holdings, "1.0000000000")
        partial = select_zusd_holding([holdings[0]], "1.0000000000")

        self.assertEqual(exact.contract_id, "00zusd-1")
        self.assertEqual(partial.contract_id, "00zusd-5")

    def test_select_holding_rejects_insufficient_balance(self):
        with self.assertRaisesRegex(ValueError, ZUSD_INSUFFICIENT_HOLDINGS_ERROR):
            select_zusd_holding([zusd_holding_candidate("00zusd-1", amount="1.0000000000")], "2.0000000000")

    def test_verified_transfer_builds_proof_only_after_completed_ledger_transfer(self):
        selected = zusd_holding_candidate("00zusd-sender", amount="5.0000000000")
        plan = SimpleNamespace(ready=True, selected_holding=selected, blockers=[])
        with patch.object(ConfiguredZUSDTransferProvider, "build_transfer_plan", return_value=plan):
            with patch.object(
                ConfiguredZUSDTransferProvider,
                "_submit_transfer_choice",
                return_value=LedgerCommandResult(
                    command_id="cmd",
                    update_id="update-zusd-transfer",
                    status="succeeded",
                    raw_response=zusd_transfer_response(),
                ),
            ):
                result = self.provider.execute_transfer(self.request)

        self.assertEqual(result.status, TRANSFER_COMPLETED)
        proof = self.provider.build_token_transfer_proof(result)
        self.assertEqual(proof["holdingCid"]["value"], "00zusd-receiver")
        self.assertEqual(proof["receiver"], EMPLOYEE_PARTY)
        self.assertEqual(proof["amount"], "1.0000000000")

    def test_wrong_receiver_or_amount_never_builds_proof(self):
        selected = zusd_holding_candidate("00zusd-sender", amount="5.0000000000")
        bad_responses = [
            zusd_transfer_response(owner=OTHER_PARTY),
            zusd_transfer_response(amount="2.0000000000"),
        ]
        for response in bad_responses:
            with self.subTest(response=response):
                plan = SimpleNamespace(ready=True, selected_holding=selected, blockers=[])
                with patch.object(ConfiguredZUSDTransferProvider, "build_transfer_plan", return_value=plan):
                    with patch.object(
                        ConfiguredZUSDTransferProvider,
                        "_submit_transfer_choice",
                        return_value=LedgerCommandResult(
                            command_id="cmd",
                            update_id="update-zusd-transfer",
                            status="succeeded",
                            raw_response=response,
                        ),
                    ):
                        result = self.provider.execute_transfer(self.request)

                self.assertEqual(result.status, TRANSFER_FAILED)
                self.assertEqual(result.proof_payload, {})
                with self.assertRaises(SettlementProofError):
                    self.provider.build_token_transfer_proof(result)

    def test_zusd_provider_rejects_usdcx_claim_token(self):
        provider = ConfiguredZUSDTransferProvider()
        with patch.dict(os.environ, {"ZALARY_TOKEN_TRANSFER_PROVIDER": ZALARY_TEST_TOKEN_PROVIDER_MODE}):
            result = provider.execute_transfer(zusd_transfer_request(token=TOKEN))

        self.assertEqual(result.status, TRANSFER_FAILED)
        self.assertIn("only settle ZUSD", result.error_message)


class USDCxTokenStandardProviderTests(TestCase):
    def setUp(self):
        self.provider = ConfiguredUSDCxTransferProvider(
            provider_mode="token_standard",
            utility_api_url="https://api.utilities.digitalasset-staging.com",
            transfer_factory_endpoint="https://api.utilities.digitalasset-staging.com/registry/transfer-instruction/v1/transfer-factory",
            allow_canonical_transfer_argument=True,
            transfer_argument_shape=TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS,
        )
        self.request = token_transfer_request()

    def test_list_usdcx_holdings_filters_owner_instrument_and_locked_contracts(self):
        contracts = [
            holding_contract("00holding-valid", amount="1200.0000000000"),
            holding_contract("00holding-wrong-owner", owner=OTHER_PARTY),
            holding_contract("00holding-wrong-instrument", instrument_id="OTHER"),
            holding_contract("00holding-locked", locked=True),
        ]

        with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
            with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                with patch("apps.zalary.services.token_transfers.usdcx.LedgerClient") as ledger_client_cls:
                    ledger_client_cls.return_value.query_active_contracts_by_interface.return_value = contracts
                    holdings = self.provider.list_usdcx_holdings(self.request)

        self.assertEqual([holding.contract_id for holding in holdings], ["00holding-valid"])
        call_kwargs = ledger_client_cls.return_value.query_active_contracts_by_interface.call_args.kwargs
        self.assertEqual(call_kwargs["interface_id"], DEFAULT_HOLDING_INTERFACE_ID)
        self.assertIn(PARTY, call_kwargs["parties"])

    def test_select_input_holdings_prefers_smallest_sufficient_set(self):
        holdings = [
            self.provider._holding_candidate_from_contract(holding_contract("00h-500", amount="500.0000000000")),
            self.provider._holding_candidate_from_contract(holding_contract("00h-600", amount="600.0000000000")),
            self.provider._holding_candidate_from_contract(holding_contract("00h-1500", amount="1500.0000000000")),
        ]

        selected_single = self.provider.select_input_holdings(holdings, "1100.0000000000")
        self.assertEqual([holding.contract_id for holding in selected_single], ["00h-1500"])

        selected_multiple = self.provider.select_input_holdings(holdings[:2], "1000.0000000000")
        self.assertEqual({holding.contract_id for holding in selected_multiple}, {"00h-500", "00h-600"})

    def test_select_input_holdings_rejects_insufficient_balance_with_exact_message(self):
        holdings = [
            self.provider._holding_candidate_from_contract(holding_contract("00h-500", amount="500.0000000000")),
        ]

        with self.assertRaisesRegex(ValueError, USDCX_INSUFFICIENT_HOLDINGS_ERROR):
            self.provider.select_input_holdings(holdings, "1100.0000000000")

    def test_holding_candidate_derives_usdcx_instrument_from_template_when_payload_omits_it(self):
        contract = {
            "contract_id": "00implicit-usdcx",
            "template_id": {
                "package_id": "pkg",
                "module_name": "P2PLending.Token.USDCx",
                "entity_name": "USDCxHolding",
            },
            "payload": {
                "owner": PARTY,
                "amount": "200.0000000000",
                "lock": None,
                "registry": PARTY,
            },
            "interface_views": {DEFAULT_HOLDING_INTERFACE_ID: {"code": 0, "message": "", "details": []}},
        }

        candidate = self.provider._holding_candidate_from_contract(contract)

        self.assertEqual(candidate.instrument["instrumentId"], "USDCx")
        self.assertTrue(candidate.amount > 0)

    def test_discover_transfer_factory_posts_nested_registry_shape(self):
        selected = [
            self.provider._holding_candidate_from_contract(holding_contract("00h-1500", amount="1500.0000000000")),
        ]
        response = SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "factoryId": "00factory",
                "transferKind": "direct",
                "choiceContext": {
                    "choiceContextData": {"ctx": "present"},
                    "disclosedContracts": [
                        {
                            "templateId": "template",
                            "contractId": "00disclosed",
                            "createdEventBlob": "redacted-in-output",
                            "synchronizerId": "sync",
                        }
                    ],
                },
            },
        )

        with patch("apps.zalary.services.token_transfers.usdcx.requests.post", return_value=response) as post:
            discovery = self.provider.discover_transfer_factory(self.request, selected_holdings=selected)

        call_kwargs = post.call_args.kwargs
        self.assertEqual(call_kwargs["json"]["choiceArguments"]["transfer"]["inputHoldings"], ["00h-1500"])
        self.assertTrue(call_kwargs["json"]["excludeDebugFields"])
        self.assertEqual(discovery.factory_id, "00factory")
        self.assertEqual(discovery.transfer_kind, "direct")
        self.assertEqual(discovery.choice_context_data, {"ctx": "present"})
        self.assertEqual(len(discovery.disclosed_contracts), 1)
        self.assertNotIn("createdEventBlob", discovery.safe_summary())
        self.assertEqual(discovery.final_choice_argument["extraArgs"]["context"], {"ctx": "present"})
        self.assertEqual(
            discovery.final_choice_argument["extraArgs"]["meta"],
            {"zalarySettlementReference": "SETTLE-payroll-001"},
        )

    def test_discover_transfer_factory_retries_canonical_flat_when_extra_args_rejected(self):
        selected = [
            self.provider._holding_candidate_from_contract(holding_contract("00h-1500", amount="1500.0000000000")),
        ]
        rejected = SimpleNamespace(status_code=400, text='{"error":"bad transfer field"}')
        accepted = SimpleNamespace(
            status_code=200,
            text="{}",
            json=lambda: {
                "factoryId": "00factory",
                "transferKind": "self",
                "choiceContext": {"choiceContextData": {}, "disclosedContracts": []},
            },
        )

        with patch(
            "apps.zalary.services.token_transfers.usdcx.requests.post",
            side_effect=[rejected, accepted],
        ) as post:
            discovery = self.provider.discover_transfer_factory(self.request, selected_holdings=selected)

        self.assertEqual(post.call_count, 2)
        self.assertEqual(discovery.argument_shape, TRANSFER_ARGUMENT_SHAPE_CANONICAL_FLAT)
        self.assertEqual(discovery.rejected_argument_shapes[0]["shape"], TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS)
        self.assertIn("sender", discovery.final_choice_argument)

    def test_build_transfer_command_plan_uses_final_choice_argument_from_registry_context(self):
        selected = [
            self.provider._holding_candidate_from_contract(holding_contract("00h-1500", amount="1500.0000000000")),
        ]
        final_argument = self.provider.build_final_choice_argument(
            request=self.request,
            selected_holdings=selected,
            choice_context_data={"context": "present"},
            shape=TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS,
        )
        discovery = TransferFactoryDiscovery(
            factory_id="00factory",
            transfer_kind="direct",
            disclosed_contracts=[{"contractId": "00disclosed"}],
            choice_context_data={"context": "present"},
            choice_context_present=True,
            final_choice_argument=final_argument,
            argument_shape=TRANSFER_ARGUMENT_SHAPE_TRANSFER_EXTRA_ARGS,
        )

        plan = self.provider.build_transfer_command_plan(
            request=self.request,
            selected_holdings=selected,
            discovery=discovery,
        )

        self.assertTrue(plan.ready)
        self.assertEqual(plan.command_payload["contractId"], "00factory")
        self.assertEqual(plan.transfer_kind, "direct")
        self.assertEqual(plan.choice_argument["transfer"]["inputHoldings"], ["00h-1500"])
        self.assertEqual(plan.choice_argument["extraArgs"]["context"], {"context": "present"})
        self.assertEqual(
            plan.choice_argument["extraArgs"]["meta"],
            {"zalarySettlementReference": "SETTLE-payroll-001"},
        )

    def test_parse_transfer_result_completed_pending_and_failed(self):
        completed = self.provider.parse_transfer_result(
            {
                "status": "Completed",
                "receiverHoldingCid": "00holding-new",
                "transferInstructionCid": "00instruction",
                "updateId": "update-1",
                "executedAt": "2026-07-01T00:00:00Z",
            },
            request=self.request,
        )
        pending = self.provider.parse_transfer_result(
            {"transferInstructionCid": "00instruction-pending", "updateId": "update-2"},
            request=self.request,
        )
        failed = self.provider.parse_transfer_result(
            {"status": "Failed", "errorMessage": "transfer rejected"},
            request=self.request,
        )

        self.assertEqual(completed.status, TRANSFER_COMPLETED)
        self.assertEqual(completed.holdingCid, "00holding-new")
        self.assertEqual(pending.status, TRANSFER_PENDING)
        self.assertEqual(failed.status, TRANSFER_FAILED)
        self.assertIn("transfer rejected", failed.error_message)

    def test_execute_transfer_returns_failed_for_insufficient_holdings(self):
        with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
            with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                with patch("apps.zalary.services.token_transfers.usdcx.LedgerClient") as ledger_client_cls:
                    ledger_client_cls.return_value.query_active_contracts_by_interface.return_value = [
                        holding_contract("00h-500", amount="500.0000000000"),
                    ]
                    result = self.provider.execute_transfer(self.request)

        self.assertEqual(result.status, TRANSFER_FAILED)
        self.assertEqual(result.error_message, USDCX_INSUFFICIENT_HOLDINGS_ERROR)


class USDCxP2PLendingProviderTests(TestCase):
    def setUp(self):
        self.provider = ConfiguredUSDCxTransferProvider(provider_mode=P2PLENDING_PROVIDER_MODE)
        self.request = token_transfer_request(amount="1.0000000000")

    def test_provider_selection_p2plending_custom(self):
        with patch.dict(
            os.environ,
            {
                "ZALARY_TOKEN_TRANSFER_PROVIDER": "usdcx",
                "ZALARY_USDCX_TRANSFER_PROVIDER": P2PLENDING_PROVIDER_MODE,
            },
        ):
            provider = get_token_transfer_provider()

        self.assertIsInstance(provider, ConfiguredUSDCxTransferProvider)
        self.assertEqual(provider.provider_mode, P2PLENDING_PROVIDER_MODE)

    def test_dry_run_fails_if_registry_contract_missing(self):
        holding = p2p_holding_contract("00holding", amount="5000.0000000000")

        with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
            with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                with patch("apps.zalary.services.token_transfers.usdcx.LedgerClient") as ledger_client_cls:
                    ledger = ledger_client_cls.return_value
                    ledger.query_active_contracts_by_interface.return_value = [holding]
                    ledger.query_active_contracts_by_template_identifier.return_value = []

                    plan = self.provider.build_p2plending_transfer_plan(self.request)

        self.assertFalse(plan.ready)
        self.assertTrue(plan.split_required)
        self.assertIn("USDCxRegistry", "; ".join(plan.blockers))

    def test_dry_run_fails_if_schema_unknown(self):
        holding = p2p_holding_contract("00holding", amount="1.0000000000")
        registry = p2p_registry_contract("00registry")

        with patch.object(
            ConfiguredUSDCxTransferProvider,
            "p2plending_schema_summary",
            return_value={"schema_confirmed": False, "choices": {}},
        ):
            with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
                with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                    with patch("apps.zalary.services.token_transfers.usdcx.LedgerClient") as ledger_client_cls:
                        ledger = ledger_client_cls.return_value
                        ledger.query_active_contracts_by_interface.return_value = [holding]
                        ledger.query_active_contracts_by_template_identifier.return_value = [registry]

                        plan = self.provider.build_p2plending_transfer_plan(self.request)

        self.assertFalse(plan.ready)
        self.assertIn("schema is not confirmed", "; ".join(plan.blockers))

    def test_dry_run_fails_if_split_required_but_split_schema_unavailable(self):
        holding = p2p_holding_contract("00holding", amount="5000.0000000000")
        registry = p2p_registry_contract("00registry")

        with patch.dict(P2PLENDING_SCHEMA, {}, clear=True):
            with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
                with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                    with patch("apps.zalary.services.token_transfers.usdcx.LedgerClient") as ledger_client_cls:
                        ledger = ledger_client_cls.return_value
                        ledger.query_active_contracts_by_interface.return_value = [holding]
                        ledger.query_active_contracts_by_template_identifier.return_value = [registry]

                        plan = self.provider.build_p2plending_transfer_plan(self.request)

        self.assertFalse(plan.ready)
        self.assertIn("Split is required", "; ".join(plan.blockers))

    def test_receiver_holding_owner_mismatch_blocks_completed_transfer(self):
        plan = self._ready_plan()
        bad_response = p2p_created_holding_response("00receiver", owner=OTHER_PARTY, amount="1.0000000000")

        with patch.object(ConfiguredUSDCxTransferProvider, "build_p2plending_transfer_plan", return_value=plan):
            with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
                with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                    with patch.object(
                        ConfiguredUSDCxTransferProvider,
                        "_submit_p2plending_choice",
                        return_value=LedgerCommandResult(
                            command_id="cmd",
                            update_id="update",
                            status="succeeded",
                            raw_response=bad_response,
                        ),
                    ):
                        result = self.provider.execute_transfer(self.request)

        self.assertEqual(result.status, TRANSFER_FAILED)
        self.assertIn("verified receiver holding", result.error_message)
        self.assertEqual(result.proof_payload, {})

    def test_receiver_holding_amount_mismatch_blocks_completed_transfer(self):
        plan = self._ready_plan()
        bad_response = p2p_created_holding_response("00receiver", owner=EMPLOYEE_PARTY, amount="2.0000000000")

        with patch.object(ConfiguredUSDCxTransferProvider, "build_p2plending_transfer_plan", return_value=plan):
            with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
                with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                    with patch.object(
                        ConfiguredUSDCxTransferProvider,
                        "_submit_p2plending_choice",
                        return_value=LedgerCommandResult(
                            command_id="cmd",
                            update_id="update",
                            status="succeeded",
                            raw_response=bad_response,
                        ),
                    ):
                        result = self.provider.execute_transfer(self.request)

        self.assertEqual(result.status, TRANSFER_FAILED)
        self.assertIn("verified receiver holding", result.error_message)
        self.assertEqual(result.proof_payload, {})

    def test_proof_only_built_on_completed_verified_transfer(self):
        plan = self._ready_plan()
        response = p2p_created_holding_response("00receiver", owner=EMPLOYEE_PARTY, amount="1.0000000000")

        with patch.object(ConfiguredUSDCxTransferProvider, "build_p2plending_transfer_plan", return_value=plan):
            with patch("apps.zalary.services.token_transfers.usdcx.load_ledger_auth_settings"):
                with patch("apps.zalary.services.token_transfers.usdcx.default_read_parties", return_value=[PARTY]):
                    with patch.object(
                        ConfiguredUSDCxTransferProvider,
                        "_submit_p2plending_choice",
                        return_value=LedgerCommandResult(
                            command_id="cmd",
                            update_id="update",
                            status="succeeded",
                            raw_response=response,
                        ),
                    ):
                        result = self.provider.execute_transfer(self.request)

        self.assertEqual(result.status, TRANSFER_COMPLETED)
        proof = self.provider.build_token_transfer_proof(result)
        self.assertEqual(proof["holdingCid"], "00receiver")
        self.assertEqual(proof["sender"], PARTY)
        self.assertEqual(proof["receiver"], EMPLOYEE_PARTY)
        self.assertEqual(proof["amount"], "1.0000000000")

    def test_pending_failed_unavailable_never_build_proof(self):
        for transfer_status in (TRANSFER_PENDING, TRANSFER_FAILED, TRANSFER_UNAVAILABLE):
            with self.subTest(transfer_status=transfer_status):
                with self.assertRaises(SettlementProofError):
                    self.provider.build_token_transfer_proof(
                        TokenTransferResult(status=transfer_status, provider_name="usdcx")
                    )

    def _ready_plan(self) -> P2PLendingTransferPlan:
        holding = self.provider._holding_candidate_from_contract(
            p2p_holding_contract("00holding", amount="1.0000000000")
        )
        return P2PLendingTransferPlan(
            ready=True,
            selected_holding=holding,
            exact_amount_holding=holding,
            registry_contract=p2p_registry_contract("00registry"),
            split_required=False,
            act_as=PARTY,
            transfer_argument={
                "sender": PARTY,
                "holdingCid": "00holding",
                "newOwner": EMPLOYEE_PARTY,
            },
            schema=self.provider.p2plending_schema_summary(),
        )


class EnrollmentManagementCommandTests(TestCase):
    def setUp(self):
        self.company = create_company_mirror(company_id="zalary-demo-001")

    @patch("apps.zalary.services.enrollment.LedgerClient")
    def test_create_demo_enrollment_allow_existing_does_not_submit(self, ledger_client_cls):
        EmployeeEnrollmentMirror.objects.create(
            contract_id="00enrollment",
            company_id=self.company.company_id,
            company_admin_party=PARTY,
            hr_wallet_party=PARTY,
            employer_wallet_party=PARTY,
            employee_wallet_party=EMPLOYEE_PARTY,
            employee_external_id="EMP-001",
            is_active=True,
        )
        stdout = StringIO()

        call_command(
            "zalary_create_demo_enrollment",
            company_id=self.company.company_id,
            employee_external_id="EMP-001",
            employee_wallet=EMPLOYEE_PARTY,
            allow_existing=True,
            stdout=stdout,
        )

        self.assertIn('"status": "exists"', stdout.getvalue())
        ledger_client_cls.assert_not_called()
        self.assertFalse(LedgerCommand.objects.exists())


class CompanyApiTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="platform-admin", password="test")
        self.user.is_staff = True
        self.user.save()
        self.client.force_authenticate(self.user)

    def test_api_requires_company_admin_when_demo_flag_disabled(self):
        with patch.dict(os.environ, {"ZALARY_ALLOW_SINGLE_PARTY_DEMO": "false"}):
            response = self.client.post(
                "/api/zalary/companies/create/",
                {
                    "companyName": "Zalary Demo Company",
                    "companyId": "zalary-demo-001",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("companyAdmin is required", response.data["error"])

    def test_api_allows_missing_company_admin_when_demo_flag_enabled(self):
        result = SimpleNamespace(
            safe_summary=lambda: {
                "status": "ok",
                "company_id": "zalary-demo-001",
            }
        )
        with patch.dict(os.environ, {"ZALARY_ALLOW_SINGLE_PARTY_DEMO": "true"}):
            with patch("apps.zalary.api.create_company_command", return_value=result) as create_company_mock:
                response = self.client.post(
                    "/api/zalary/companies/create/",
                    {
                        "companyName": "Zalary Demo Company",
                        "companyId": "zalary-demo-001",
                    },
                    format="json",
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(create_company_mock.call_args.kwargs["allow_single_party_demo"])

    def test_api_enrollment_create_fails_if_preflight_fails(self):
        company = create_company_mirror(company_id="zalary-demo-001")

        response = self.client.post(
            f"/api/zalary/companies/{company.company_id}/enrollments/create/",
            {
                "hrWallet": OTHER_PARTY,
                "employerWallet": PARTY,
                "employeeWallet": EMPLOYEE_PARTY,
                "employeeExternalId": "EMP-001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("hrWallet is not authorized", response.data["error"])
        self.assertFalse(LedgerCommand.objects.exists())

    def test_salary_claim_request_rejects_unbound_user_by_default(self):
        create_company_mirror(company_id="zalary-demo-001")
        create_payroll_vault_mirror(
            company_id="zalary-demo-001",
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        create_salary_allocation_mirror(
            company_id="zalary-demo-001",
            payroll_id="payroll-001",
            status="AllocationClaimTicketIssued",
        )
        create_claim_ticket_mirror(company_id="zalary-demo-001", payroll_id="payroll-001")

        response = self.client.post(
            "/api/zalary/salary-claims/request/",
            {
                "company_id": "zalary-demo-001",
                "payroll_id": "payroll-001",
                "employee_external_id": "EMP-001",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "auth_party_not_bound")
        self.assertFalse(LedgerCommand.objects.exists())

    def test_salary_claim_request_dev_bypass_returns_envelope(self):
        create_company_mirror(company_id="zalary-demo-001")
        create_payroll_vault_mirror(
            company_id="zalary-demo-001",
            payroll_id="payroll-001",
            status="Active",
            uploaded_count=1,
            total_net_pay="1100.0000000000",
        )
        create_salary_allocation_mirror(
            company_id="zalary-demo-001",
            payroll_id="payroll-001",
            status="AllocationClaimTicketIssued",
        )
        create_claim_ticket_mirror(company_id="zalary-demo-001", payroll_id="payroll-001")
        result = SimpleNamespace(
            status="ok",
            safe_summary=lambda: {
                "status": "ok",
                "action": "RequestSalaryClaim",
                "command_id": "cmd-1",
                "update_id": "update-1",
                "ledger_command_id": 1,
                "company_id": "zalary-demo-001",
                "payroll_id": "payroll-001",
                "employee_external_id": "EMP-001",
                "contract_id": "00claim",
                "choice_name": "RequestSalaryClaim",
            },
        )

        with patch.dict(os.environ, {"ZALARY_ALLOW_UNBOUND_DEMO_AUTH": "true"}):
            with patch("apps.zalary.api.request_salary_claim", return_value=result):
                response = self.client.post(
                    "/api/zalary/salary-claims/request/",
                    {
                        "company_id": "zalary-demo-001",
                        "payroll_id": "payroll-001",
                        "employee_external_id": "EMP-001",
                    },
                    format="json",
                )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["ledger"]["command_id"], "cmd-1")
        self.assertEqual(response.data["next_actions"], ["confirm_settlement"])
