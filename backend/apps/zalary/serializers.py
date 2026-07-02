from rest_framework import serializers

from .models import (
    ClaimTicketMirror,
    CompanyMirror,
    EmployeeEnrollmentMirror,
    FailedSalaryClaimMirror,
    LedgerCommand,
    LedgerContract,
    PayslipMirror,
    PayrollVaultMirror,
    SalaryAllocationMirror,
    SalaryClaimMirror,
    SettledSalaryRecordMirror,
    SettlementReceiptMirror,
    SettlementProofMirror,
    ZalaryConfigMirror,
    ZUSDFaucetRequest,
    ZUSDHoldingMirror,
)


class TokenInstrumentSerializer(serializers.Serializer):
    symbol = serializers.CharField()
    instrumentId = serializers.CharField()
    instrumentAdmin = serializers.CharField()
    utilityApiUrl = serializers.URLField()
    xReserveApiUrl = serializers.URLField()


class PayrollPeriodSerializer(serializers.Serializer):
    label = serializers.CharField()
    startsAt = serializers.DateField()
    endsAt = serializers.DateField()


class SalaryBreakdownSerializer(serializers.Serializer):
    grossPay = serializers.DecimalField(max_digits=36, decimal_places=10)
    allowances = serializers.DecimalField(max_digits=36, decimal_places=10)
    deductions = serializers.DecimalField(max_digits=36, decimal_places=10)
    netPay = serializers.DecimalField(max_digits=36, decimal_places=10)
    token = TokenInstrumentSerializer()


class TokenTransferProofSerializer(serializers.Serializer):
    token = TokenInstrumentSerializer()
    sender = serializers.CharField()
    receiver = serializers.CharField()
    amount = serializers.DecimalField(max_digits=36, decimal_places=10)
    transferReference = serializers.CharField()
    transferInstructionCid = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    holdingCid = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    executedAt = serializers.DateTimeField()


class CreateCompanyRequestSerializer(serializers.Serializer):
    platform_config_contract_id = serializers.CharField(required=False, allow_blank=True)
    companyAdmin = serializers.CharField(required=False, allow_blank=True)
    companyName = serializers.CharField()
    companyId = serializers.CharField()
    adminWallets = serializers.ListField(child=serializers.CharField(), allow_empty=False, required=False)
    hrWallets = serializers.ListField(child=serializers.CharField(), allow_empty=False, required=False)
    employerWallets = serializers.ListField(child=serializers.CharField(), allow_empty=False, required=False)
    allowedTokens = serializers.ListField(child=TokenInstrumentSerializer(), allow_empty=False, required=False)


class CreateEmployeeEnrollmentRequestSerializer(serializers.Serializer):
    company_contract_id = serializers.CharField()
    hrWallet = serializers.CharField()
    employerWallet = serializers.CharField()
    employeeWallet = serializers.CharField()
    employeeExternalId = serializers.CharField()


class EmployeeEnrollmentPreflightSerializer(serializers.Serializer):
    hrWallet = serializers.CharField()
    employerWallet = serializers.CharField()
    employeeWallet = serializers.CharField()
    employeeExternalId = serializers.CharField()


class CreatePayrollVaultRequestSerializer(serializers.Serializer):
    company_contract_id = serializers.CharField()
    hrWallet = serializers.CharField()
    employerWallet = serializers.CharField()
    payrollId = serializers.CharField()
    payrollPeriod = PayrollPeriodSerializer()
    payrollToken = TokenInstrumentSerializer()
    claimWindowStart = serializers.DateTimeField()
    claimWindowEnd = serializers.DateTimeField()
    expectedEmployeeCount = serializers.IntegerField(min_value=1)


class AddSalaryAllocationRequestSerializer(serializers.Serializer):
    payroll_vault_contract_id = serializers.CharField()
    allocationEmployeeWallet = serializers.CharField()
    employeeExternalId = serializers.CharField()
    salaryBreakdown = SalaryBreakdownSerializer()
    enrollmentCid = serializers.CharField()


class DemoPayrollPipelineRequestSerializer(serializers.Serializer):
    payrollId = serializers.CharField(required=False, allow_blank=True)
    employeeExternalId = serializers.CharField(required=False, default="EMP-001")
    grossPay = serializers.DecimalField(max_digits=36, decimal_places=10, required=False, default="1000")
    allowances = serializers.DecimalField(max_digits=36, decimal_places=10, required=False, default="200")
    deductions = serializers.DecimalField(max_digits=36, decimal_places=10, required=False, default="100")
    netPay = serializers.DecimalField(max_digits=36, decimal_places=10, required=False, default="1100")
    allowExisting = serializers.BooleanField(required=False, default=False)


class DemoFundingTicketPipelineRequestSerializer(serializers.Serializer):
    payrollId = serializers.CharField(required=False, allow_blank=True, default="zalary-payroll-demo-001")
    employeeExternalId = serializers.CharField(required=False, default="EMP-001")
    fundingAmount = serializers.DecimalField(max_digits=36, decimal_places=10, required=False, allow_null=True)
    fundingReference = serializers.CharField(required=False, allow_blank=True)
    allowExisting = serializers.BooleanField(required=False, default=False)


class RequestSalaryClaimSerializer(serializers.Serializer):
    claim_ticket_contract_id = serializers.CharField(required=False, allow_blank=True)
    company_id = serializers.CharField(required=False, allow_blank=True)
    payroll_id = serializers.CharField(required=False, allow_blank=True)
    employee_external_id = serializers.CharField(required=False, allow_blank=True)
    allowExisting = serializers.BooleanField(required=False, default=False)


class ConfirmSettlementRequestSerializer(serializers.Serializer):
    salary_claim_contract_id = serializers.CharField(required=False, allow_blank=True)
    company_id = serializers.CharField(required=False, allow_blank=True)
    payroll_id = serializers.CharField(required=False, allow_blank=True)
    employee_external_id = serializers.CharField(required=False, allow_blank=True)
    payrollVaultCid = serializers.CharField(required=False, allow_blank=True)
    settlementReference = serializers.CharField()
    settlementProof = TokenTransferProofSerializer(required=False, allow_null=True)
    allowExisting = serializers.BooleanField(required=False, default=False)
    demoProof = serializers.BooleanField(required=False, default=False)


class DemoFullPayrollExecutionRequestSerializer(serializers.Serializer):
    payrollId = serializers.CharField(required=False, allow_blank=True, default="zalary-payroll-demo-001")
    employeeExternalId = serializers.CharField(required=False, default="EMP-001")
    fundingAmount = serializers.DecimalField(max_digits=36, decimal_places=10, required=False, allow_null=True)
    fundingReference = serializers.CharField(required=False, allow_blank=True)
    settlementReference = serializers.CharField(required=False, allow_blank=True)
    allowExisting = serializers.BooleanField(required=False, default=False)
    demoProof = serializers.BooleanField(required=False, default=False)


class LedgerContractSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerContract
        fields = "__all__"


class LedgerCommandSerializer(serializers.ModelSerializer):
    class Meta:
        model = LedgerCommand
        fields = "__all__"


class ZalaryConfigMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ZalaryConfigMirror
        fields = "__all__"


class ZalaryConfigOutputSerializer(serializers.ModelSerializer):
    ledger_contract_id = serializers.CharField(source="contract_id")
    platform_admin = serializers.CharField(source="platform_admin_party")
    created_at = serializers.SerializerMethodField()
    sync_timestamp = serializers.DateTimeField(source="synced_at")
    last_seen = serializers.DateTimeField(source="last_seen_at", allow_null=True)

    class Meta:
        model = ZalaryConfigMirror
        fields = [
            "ledger_contract_id",
            "package_name",
            "template_id",
            "platform_admin",
            "supported_tokens",
            "default_token",
            "is_active",
            "ledger_active",
            "created_at",
            "sync_timestamp",
            "last_seen",
        ]

    def get_created_at(self, obj):
        if obj.payload.get("createdAt"):
            return obj.payload["createdAt"]
        if obj.ledger_created_at:
            return obj.ledger_created_at.isoformat()
        return None


class CompanyMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyMirror
        fields = "__all__"


class EmployeeEnrollmentMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeEnrollmentMirror
        fields = "__all__"


class PayrollVaultMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollVaultMirror
        fields = "__all__"


class SalaryAllocationMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryAllocationMirror
        fields = "__all__"


class ClaimTicketMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaimTicketMirror
        fields = "__all__"


class SalaryClaimMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalaryClaimMirror
        fields = "__all__"


class SettlementReceiptMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = SettlementReceiptMirror
        fields = "__all__"


class PayslipMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipMirror
        fields = "__all__"


class SettledSalaryRecordMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = SettledSalaryRecordMirror
        fields = "__all__"


class FailedSalaryClaimMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = FailedSalaryClaimMirror
        fields = "__all__"


class SettlementProofMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = SettlementProofMirror
        fields = "__all__"


class ZUSDHoldingMirrorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ZUSDHoldingMirror
        fields = "__all__"


class ZUSDFaucetRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ZUSDFaucetRequest
        fields = "__all__"


class ZUSDFaucetMintRequestSerializer(serializers.Serializer):
    owner_party = serializers.CharField()
    amount = serializers.DecimalField(max_digits=36, decimal_places=10)
    reference = serializers.CharField(required=False, allow_blank=True)
    request_id = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)
