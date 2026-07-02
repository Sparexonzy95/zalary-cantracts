from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import selectors
from .permissions import IsCompanyAdmin, IsEmployeeWallet, IsEmployerWallet, IsHRWallet, IsPlatformAdmin
from .serializers import (
    AddSalaryAllocationRequestSerializer,
    CompanyMirrorSerializer,
    ConfirmSettlementRequestSerializer,
    CreateCompanyRequestSerializer,
    CreateEmployeeEnrollmentRequestSerializer,
    CreatePayrollVaultRequestSerializer,
    DemoFullPayrollExecutionRequestSerializer,
    DemoFundingTicketPipelineRequestSerializer,
    DemoPayrollPipelineRequestSerializer,
    EmployeeEnrollmentPreflightSerializer,
    EmployeeEnrollmentMirrorSerializer,
    LedgerCommandSerializer,
    PayrollVaultMirrorSerializer,
    RequestSalaryClaimSerializer,
    SalaryAllocationMirrorSerializer,
    SalaryClaimMirrorSerializer,
    ZalaryConfigMirrorSerializer,
    ZalaryConfigOutputSerializer,
    ZUSDFaucetMintRequestSerializer,
)
from .services.auth import auth_configured, ledger_api_url_configured, load_ledger_auth_settings, single_party_demo_enabled
from .services.commands import create_company as create_company_command
from .services.enrollment import create_employee_enrollment, preflight_employee_enrollment
from .services.errors import (
    ConfigurationError,
    DuplicateClaimTicketError,
    DuplicateCompanyError,
    DuplicateEnrollmentError,
    DuplicatePayrollVaultError,
    DuplicateSalaryAllocationError,
    DuplicateSalaryClaimError,
    DuplicateSettlementError,
    AuthBindingError,
    OnboardingValidationError,
    SettlementProofError,
    ZalaryBackendError,
    safe_error_message,
)
from .services.faucet import get_zusd_balance, request_zusd_faucet_mint, zusd_faucet_history
from .services.ledger import LedgerClient
from .services.payroll import create_demo_payroll_pipeline, preflight_demo_payroll_pipeline
from .services.payroll import (
    create_demo_funding_activation_ticket_pipeline,
    preflight_demo_funding_activation_ticket_pipeline,
)
from .services.roles import company_role_summary
from .services.request_context import require_party_for_role
from .services.responses import action_success_envelope, error_envelope
from .services.settlement import (
    confirm_salary_settlement,
    create_demo_full_payroll_execution,
    preflight_request_salary_claim,
    request_salary_claim,
)
from .services.sync import sync_zalary_config


def _not_implemented(feature: str) -> Response:
    return Response(
        {
            "detail": f"{feature} is scaffolded but not implemented yet.",
            "status": "not_implemented",
        },
        status=status.HTTP_501_NOT_IMPLEMENTED,
    )


class LedgerHealthView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        response = {
            "status": "ok",
            "ledger_api_url_configured": ledger_api_url_configured(),
            "auth_configured": auth_configured(),
            "ledger_end": None,
        }

        try:
            settings = load_ledger_auth_settings()
            response["ledger_end"] = LedgerClient(settings).get_current_ledger_offset()
            return Response(response)
        except ZalaryBackendError as exc:
            response["status"] = "error"
            response["error"] = safe_error_message(exc)
            return Response(response, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class ZalaryConfigListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        configs = selectors.synced_zalary_configs()
        return Response(
            {
                "count": configs.count(),
                "results": ZalaryConfigOutputSerializer(configs, many=True).data,
            }
        )


class ZalaryConfigSyncView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            result = sync_zalary_config()
            configs = selectors.synced_zalary_configs()
            return Response(
                {
                    "status": "ok",
                    "synced_count": result.synced_count,
                    "marked_inactive_count": result.marked_inactive_count,
                    "contract_ids": result.contract_ids,
                    "count": configs.count(),
                    "results": ZalaryConfigOutputSerializer(configs, many=True).data,
                }
            )
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class ZalaryConfigViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ZalaryConfigMirrorSerializer
    permission_classes = [IsPlatformAdmin]

    def get_queryset(self):
        return selectors.ZalaryConfigMirror.objects.order_by("-synced_at")

    @action(detail=False, methods=["post"], url_path="sync")
    def sync(self, request):
        try:
            result = sync_zalary_config()
            queryset = self.get_queryset()
            return Response(
                {
                    "status": "ok",
                    "synced_count": result.synced_count,
                    "marked_inactive_count": result.marked_inactive_count,
                    "contract_ids": result.contract_ids,
                    "count": queryset.count(),
                    "results": ZalaryConfigOutputSerializer(queryset, many=True).data,
                }
            )
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class ZUSDBalanceView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            result = get_zusd_balance(owner_party=request.query_params.get("owner_party", ""))
            return Response({"status": "ok", **result.safe_summary()})
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST if isinstance(exc, ConfigurationError) else status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class ZUSDFaucetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ZUSDFaucetMintRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = request_zusd_faucet_mint(
                owner_party=data["owner_party"],
                amount=data["amount"],
                reference=data.get("reference"),
                request_id=data.get("request_id"),
                metadata=data.get("metadata") or {},
            )
            return Response(result.safe_summary(), status=status.HTTP_201_CREATED)
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST if isinstance(exc, ConfigurationError) else status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class ZUSDFaucetHistoryView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "results": zusd_faucet_history(owner_party=request.query_params.get("owner_party")),
            }
        )


class CompanyViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CompanyMirrorSerializer
    permission_classes = [IsCompanyAdmin]
    lookup_field = "company_id"
    lookup_value_regex = "[^/]+"

    def get_queryset(self):
        return selectors.CompanyMirror.objects.order_by("company_id")

    @action(detail=False, methods=["post"], url_path="create", permission_classes=[IsPlatformAdmin])
    def create_company(self, request):
        serializer = CreateCompanyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        allow_single_party_demo = single_party_demo_enabled()
        if not allow_single_party_demo and not data.get("companyAdmin"):
            return Response(
                {
                    "status": "error",
                    "error": "companyAdmin is required unless ZALARY_ALLOW_SINGLE_PARTY_DEMO=true.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = create_company_command(
                platform_config_contract_id=data.get("platform_config_contract_id"),
                company_admin=data.get("companyAdmin"),
                company_name=data["companyName"],
                company_id=data["companyId"],
                admin_wallets=data.get("adminWallets"),
                hr_wallets=data.get("hrWallets"),
                employer_wallets=data.get("employerWallets"),
                allowed_tokens=data.get("allowedTokens"),
                sync_after=True,
                allow_single_party_demo=allow_single_party_demo,
            )
            return Response(result.safe_summary(), status=status.HTTP_201_CREATED)
        except DuplicateCompanyError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except OnboardingValidationError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(detail=True, methods=["post"], url_path="allowed-tokens")
    def update_allowed_tokens(self, request, pk=None):
        return _not_implemented("Company allowed-token update command submission")

    @action(detail=True, methods=["get"], url_path="roles")
    def roles(self, request, company_id=None):
        return Response(company_role_summary(self.get_object()))

    @action(
        detail=True,
        methods=["post"],
        url_path="enrollments/preflight",
        permission_classes=[IsHRWallet | IsCompanyAdmin | IsPlatformAdmin],
    )
    def enrollment_preflight(self, request, company_id=None):
        serializer = EmployeeEnrollmentPreflightSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = self.get_object()
        try:
            result = preflight_employee_enrollment(
                company_id=company.company_id,
                hr_wallet=data["hrWallet"],
                employer_wallet=data["employerWallet"],
                employee_wallet=data["employeeWallet"],
                employee_external_id=data["employeeExternalId"],
            )
            return Response(result.safe_summary())
        except OnboardingValidationError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="enrollments/create",
        permission_classes=[IsHRWallet | IsCompanyAdmin | IsPlatformAdmin],
    )
    def enrollment_create(self, request, company_id=None):
        serializer = EmployeeEnrollmentPreflightSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = self.get_object()
        try:
            result = create_employee_enrollment(
                company_id=company.company_id,
                hr_wallet=data["hrWallet"],
                employer_wallet=data["employerWallet"],
                employee_wallet=data["employeeWallet"],
                employee_external_id=data["employeeExternalId"],
                sync_after=True,
            )
            return Response(result.safe_summary(), status=status.HTTP_201_CREATED)
        except DuplicateEnrollmentError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except OnboardingValidationError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="payroll-pipeline/preflight",
        permission_classes=[IsHRWallet | IsCompanyAdmin | IsPlatformAdmin],
    )
    def payroll_pipeline_preflight(self, request, company_id=None):
        serializer = DemoPayrollPipelineRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = self.get_object()
        try:
            result = preflight_demo_payroll_pipeline(
                company_id=company.company_id,
                payroll_id=data.get("payrollId") or None,
                employee_external_id=data.get("employeeExternalId") or "EMP-001",
                gross_pay=data.get("grossPay"),
                allowances=data.get("allowances"),
                deductions=data.get("deductions"),
                net_pay=data.get("netPay"),
            )
            return Response(result.safe_summary())
        except (DuplicatePayrollVaultError, DuplicateSalaryAllocationError) as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except OnboardingValidationError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="payroll-pipeline/create-demo",
        permission_classes=[IsHRWallet | IsCompanyAdmin | IsPlatformAdmin],
    )
    def payroll_pipeline_create_demo(self, request, company_id=None):
        serializer = DemoPayrollPipelineRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = self.get_object()
        try:
            result = create_demo_payroll_pipeline(
                company_id=company.company_id,
                payroll_id=data.get("payrollId") or None,
                employee_external_id=data.get("employeeExternalId") or "EMP-001",
                gross_pay=data.get("grossPay"),
                allowances=data.get("allowances"),
                deductions=data.get("deductions"),
                net_pay=data.get("netPay"),
                allow_existing=bool(data.get("allowExisting")),
            )
            return Response(result.safe_summary(), status=status.HTTP_201_CREATED)
        except (DuplicatePayrollVaultError, DuplicateSalaryAllocationError) as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except OnboardingValidationError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="funding-ticket-pipeline/preflight",
        permission_classes=[IsHRWallet | IsCompanyAdmin | IsPlatformAdmin],
    )
    def funding_ticket_pipeline_preflight(self, request, company_id=None):
        serializer = DemoFundingTicketPipelineRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = self.get_object()
        try:
            result = preflight_demo_funding_activation_ticket_pipeline(
                company_id=company.company_id,
                payroll_id=data.get("payrollId") or "zalary-payroll-demo-001",
                employee_external_id=data.get("employeeExternalId") or "EMP-001",
                funding_amount=data.get("fundingAmount"),
                funding_reference=data.get("fundingReference") or None,
            )
            return Response(result.safe_summary())
        except DuplicateClaimTicketError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except OnboardingValidationError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="funding-ticket-pipeline/create-demo",
        permission_classes=[IsHRWallet | IsCompanyAdmin | IsPlatformAdmin],
    )
    def funding_ticket_pipeline_create_demo(self, request, company_id=None):
        serializer = DemoFundingTicketPipelineRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = self.get_object()
        try:
            result = create_demo_funding_activation_ticket_pipeline(
                company_id=company.company_id,
                payroll_id=data.get("payrollId") or "zalary-payroll-demo-001",
                employee_external_id=data.get("employeeExternalId") or "EMP-001",
                funding_amount=data.get("fundingAmount"),
                funding_reference=data.get("fundingReference") or None,
                allow_existing=bool(data.get("allowExisting")),
            )
            status_code = status.HTTP_200_OK if result.status == "pending_claim_window_open" else status.HTTP_201_CREATED
            return Response(result.safe_summary(), status=status_code)
        except DuplicateClaimTicketError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_409_CONFLICT,
            )
        except OnboardingValidationError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ZalaryBackendError as exc:
            return Response(
                {
                    "status": "error",
                    "error": safe_error_message(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(
        detail=True,
        methods=["post"],
        url_path="full-payroll-execution/create-demo",
        permission_classes=[IsHRWallet | IsCompanyAdmin | IsPlatformAdmin],
    )
    def full_payroll_execution_create_demo(self, request, company_id=None):
        serializer = DemoFullPayrollExecutionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        company = self.get_object()
        try:
            require_party_for_role(
                request,
                "company_admin",
                [company.company_admin_party] + (company.admin_wallet_parties or []),
            )
            result = create_demo_full_payroll_execution(
                company_id=company.company_id,
                payroll_id=data.get("payrollId") or "zalary-payroll-demo-001",
                employee_external_id=data.get("employeeExternalId") or "EMP-001",
                funding_amount=data.get("fundingAmount"),
                funding_reference=data.get("fundingReference") or None,
                settlement_reference=data.get("settlementReference") or None,
                allow_existing=bool(data.get("allowExisting")),
                demo_proof=bool(data.get("demoProof")),
            )
            status_code = status.HTTP_200_OK if result.settlement_step.status == "exists" else status.HTTP_201_CREATED
            summary = result.safe_summary()
            settlement_envelope = action_success_envelope(result.settlement_step)
            return Response(
                {
                    "status": summary["status"],
                    "action": "FullPayrollExecution",
                    "resource": {
                        "company_id": summary["company_id"],
                        "payroll_id": summary["payroll_id"],
                        "employee_external_id": summary["employee_external_id"],
                    },
                    "ledger": settlement_envelope["ledger"],
                    "transfer": settlement_envelope["transfer"],
                    "sync": summary.get("final_sync", {}),
                    "next_actions": ["view_payslip"],
                    "steps": {
                        "setup": summary["setup_step"],
                        "salary_claim": summary["salary_claim_step"],
                        "settlement": summary["settlement_step"],
                    },
                },
                status=status_code,
            )
        except AuthBindingError as exc:
            return Response(
                error_envelope(code="auth_party_not_bound", message=safe_error_message(exc)),
                status=status.HTTP_403_FORBIDDEN,
            )
        except (DuplicateSalaryClaimError, DuplicateSettlementError) as exc:
            return Response(
                error_envelope(code="duplicate_payroll_execution", message=safe_error_message(exc)),
                status=status.HTTP_409_CONFLICT,
            )
        except (OnboardingValidationError, SettlementProofError) as exc:
            return Response(
                error_envelope(code="payroll_execution_invalid", message=safe_error_message(exc)),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ZalaryBackendError as exc:
            return Response(
                error_envelope(code="payroll_execution_unavailable", message=safe_error_message(exc)),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class EnrollmentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = EmployeeEnrollmentMirrorSerializer
    permission_classes = [IsHRWallet]

    def get_queryset(self):
        return selectors.EmployeeEnrollmentMirror.objects.order_by("company_id", "employee_external_id")

    def create(self, request):
        serializer = CreateEmployeeEnrollmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _not_implemented("Employee enrollment command submission")


class PayrollVaultViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PayrollVaultMirrorSerializer
    permission_classes = [IsHRWallet | IsEmployerWallet]

    def get_queryset(self):
        return selectors.PayrollVaultMirror.objects.order_by("-synced_at")

    def create(self, request):
        serializer = CreatePayrollVaultRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _not_implemented("Payroll vault creation command submission")

    @action(detail=True, methods=["post"], url_path="finalize-allocations")
    def finalize_allocations(self, request, pk=None):
        return _not_implemented("Payroll allocation finalization command submission")

    @action(detail=True, methods=["post"], url_path="confirm-funding")
    def confirm_funding(self, request, pk=None):
        return _not_implemented("Payroll funding confirmation command submission")

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        return _not_implemented("Payroll activation command submission")

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        return _not_implemented("Payroll closure command submission")

    @action(detail=True, methods=["post"], url_path="withdraw-leftovers")
    def withdraw_leftovers(self, request, pk=None):
        return _not_implemented("Leftover withdrawal command submission")


class SalaryAllocationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SalaryAllocationMirrorSerializer
    permission_classes = [IsHRWallet | IsEmployerWallet]

    def get_queryset(self):
        return selectors.SalaryAllocationMirror.objects.order_by("company_id", "payroll_id", "employee_external_id")

    def create(self, request):
        serializer = AddSalaryAllocationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _not_implemented("Salary allocation command submission")

    @action(detail=True, methods=["post"], url_path="issue-claim-ticket")
    def issue_claim_ticket(self, request, pk=None):
        return _not_implemented("Claim ticket issuance command submission")


class SalaryClaimViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SalaryClaimMirrorSerializer
    permission_classes = [IsEmployeeWallet | IsEmployerWallet]

    def get_queryset(self):
        return selectors.SalaryClaimMirror.objects.order_by("company_id", "payroll_id", "employee_external_id")

    @action(detail=False, methods=["post"], url_path="request")
    def request_claim(self, request):
        serializer = RequestSalaryClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            preflight = preflight_request_salary_claim(
                claim_ticket_contract_id=data.get("claim_ticket_contract_id") or None,
                company_id=data.get("company_id") or None,
                payroll_id=data.get("payroll_id") or None,
                employee_external_id=data.get("employee_external_id") or None,
            )
            require_party_for_role(request, "employee", [preflight.employee_wallet])
            result = request_salary_claim(
                claim_ticket_contract_id=data.get("claim_ticket_contract_id") or None,
                company_id=data.get("company_id") or None,
                payroll_id=data.get("payroll_id") or None,
                employee_external_id=data.get("employee_external_id") or None,
                allow_existing=bool(data.get("allowExisting")),
                sync_after=True,
            )
            status_code = status.HTTP_200_OK if result.status == "exists" else status.HTTP_201_CREATED
            if result.status == "pending":
                status_code = status.HTTP_202_ACCEPTED
            return Response(action_success_envelope(result), status=status_code)
        except AuthBindingError as exc:
            return Response(
                error_envelope(code="auth_party_not_bound", message=safe_error_message(exc)),
                status=status.HTTP_403_FORBIDDEN,
            )
        except DuplicateSalaryClaimError as exc:
            return Response(
                error_envelope(code="duplicate_salary_claim", message=safe_error_message(exc)),
                status=status.HTTP_409_CONFLICT,
            )
        except OnboardingValidationError as exc:
            return Response(
                error_envelope(code="salary_claim_invalid", message=safe_error_message(exc)),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ZalaryBackendError as exc:
            return Response(
                error_envelope(code="salary_claim_unavailable", message=safe_error_message(exc)),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(detail=True, methods=["post"], url_path="confirm-settlement")
    def confirm_settlement(self, request, pk=None):
        serializer = ConfirmSettlementRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        salary_claim_contract_id = data.get("salary_claim_contract_id") or ""
        if not salary_claim_contract_id:
            claim_object = self.get_object()
            salary_claim_contract_id = claim_object.contract_id
        else:
            claim_object = self.get_queryset().filter(contract_id=salary_claim_contract_id).first()
            if claim_object is None:
                return Response(
                    error_envelope(code="salary_claim_not_found", message="SalaryClaim was not found in the local mirror."),
                    status=status.HTTP_400_BAD_REQUEST,
                )
        try:
            require_party_for_role(request, "employer", [claim_object.employer_wallet_party])
            result = confirm_salary_settlement(
                salary_claim_contract_id=salary_claim_contract_id,
                company_id=data.get("company_id") or None,
                payroll_id=data.get("payroll_id") or None,
                employee_external_id=data.get("employee_external_id") or None,
                settlement_reference=data["settlementReference"],
                settlement_proof=data.get("settlementProof"),
                demo_proof=bool(data.get("demoProof")),
                allow_existing=bool(data.get("allowExisting")),
                sync_after=True,
            )
            status_code = status.HTTP_200_OK if result.status == "exists" else status.HTTP_201_CREATED
            if result.status == "pending":
                status_code = status.HTTP_202_ACCEPTED
            return Response(action_success_envelope(result), status=status_code)
        except AuthBindingError as exc:
            return Response(
                error_envelope(code="auth_party_not_bound", message=safe_error_message(exc)),
                status=status.HTTP_403_FORBIDDEN,
            )
        except DuplicateSettlementError as exc:
            return Response(
                error_envelope(code="duplicate_settlement", message=safe_error_message(exc)),
                status=status.HTTP_409_CONFLICT,
            )
        except (OnboardingValidationError, SettlementProofError) as exc:
            return Response(
                error_envelope(code="settlement_invalid", message=safe_error_message(exc)),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ZalaryBackendError as exc:
            return Response(
                error_envelope(code="settlement_unavailable", message=safe_error_message(exc)),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        return _not_implemented("Salary claim rejection command submission")


class LedgerCommandViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LedgerCommandSerializer
    permission_classes = [IsPlatformAdmin]

    def get_queryset(self):
        return selectors.LedgerCommand.objects.order_by("-created_at")

    @action(detail=True, methods=["post"], url_path="poll")
    def poll(self, request, pk=None):
        return _not_implemented("Ledger command status polling")
