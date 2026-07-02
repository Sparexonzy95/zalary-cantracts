from django.db import models


class LedgerRole(models.TextChoices):
    PLATFORM_ADMIN = "platform_admin", "Platform admin"
    COMPANY_ADMIN = "company_admin", "Company admin"
    HR = "hr", "HR"
    EMPLOYER = "employer", "Employer"
    EMPLOYEE = "employee", "Employee"


class CommandStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING = "pending", "Pending"
    SUBMITTED = "submitted", "Submitted"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class FaucetRequestStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    MINTED = "minted", "Minted"
    FAILED = "failed", "Failed"
    REJECTED = "rejected", "Rejected"


class LedgerParty(models.Model):
    party_id = models.CharField(max_length=512, unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=32, choices=LedgerRole.choices, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["role", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.party_id


class LedgerContract(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    template_id = models.CharField(max_length=512, db_index=True)
    module_name = models.CharField(max_length=255, blank=True)
    entity_name = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    contract_key = models.JSONField(null=True, blank=True)
    signatories = models.JSONField(default=list, blank=True)
    observers = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    created_update_id = models.CharField(max_length=255, blank=True)
    archived_update_id = models.CharField(max_length=255, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["template_id", "active"]),
            models.Index(fields=["ledger_offset"]),
        ]

    def __str__(self) -> str:
        return f"{self.template_id}:{self.contract_id}"


class LedgerCommand(models.Model):
    command_id = models.CharField(max_length=255, unique=True)
    workflow_id = models.CharField(max_length=255, blank=True)
    act_as = models.JSONField(default=list, blank=True)
    read_as = models.JSONField(default=list, blank=True)
    template_id = models.CharField(max_length=512, blank=True)
    contract_id = models.CharField(max_length=1024, blank=True)
    choice_name = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=32,
        choices=CommandStatus.choices,
        default=CommandStatus.DRAFT,
        db_index=True,
    )
    update_id = models.CharField(max_length=255, blank=True, db_index=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["template_id", "choice_name"]),
        ]

    def __str__(self) -> str:
        return self.command_id


class ZalaryConfigMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    package_name = models.CharField(max_length=255, blank=True)
    template_id = models.CharField(max_length=512, blank=True)
    platform_admin_party = models.CharField(max_length=512, db_index=True)
    supported_tokens = models.JSONField(default=list, blank=True)
    default_token = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    ledger_active = models.BooleanField(default=True, db_index=True)
    ledger_created_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["template_id", "ledger_active"]),
            models.Index(fields=["platform_admin_party", "is_active"]),
        ]

    def __str__(self) -> str:
        return self.contract_id


class CompanyMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    company_name = models.CharField(max_length=255)
    platform_admin_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    admin_wallet_parties = models.JSONField(default=list, blank=True)
    hr_wallet_parties = models.JSONField(default=list, blank=True)
    employer_wallet_parties = models.JSONField(default=list, blank=True)
    allowed_tokens = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "company_admin_party"]),
        ]

    def __str__(self) -> str:
        return self.company_id


class EmployeeEnrollmentMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "employee_external_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.employee_external_id}"


class PayrollVaultMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    vault_status = models.CharField(max_length=64, db_index=True)
    payroll_period = models.JSONField(default=dict, blank=True)
    payroll_token = models.JSONField(default=dict, blank=True)
    claim_window_start = models.DateTimeField(null=True, blank=True)
    claim_window_end = models.DateTimeField(null=True, blank=True)
    totals = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id"]),
            models.Index(fields=["vault_status", "claim_window_end"]),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.payroll_id}"


class SalaryAllocationMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    allocation_status = models.CharField(max_length=64, db_index=True)
    salary_breakdown = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
            models.Index(fields=["allocation_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.payroll_id}:{self.employee_external_id}"


class ClaimTicketMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    ticket_amount = models.DecimalField(max_digits=36, decimal_places=10)
    ticket_token = models.JSONField(default=dict, blank=True)
    salary_breakdown = models.JSONField(default=dict, blank=True)
    source_allocation_contract_id = models.CharField(max_length=1024)
    claim_window_start = models.DateTimeField(null=True, blank=True)
    claim_window_end = models.DateTimeField(null=True, blank=True)
    ledger_active = models.BooleanField(default=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
            models.Index(fields=["claim_window_end"]),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.payroll_id}:{self.employee_external_id}"


class SalaryClaimMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    claim_status = models.CharField(max_length=64, db_index=True)
    claim_amount = models.DecimalField(max_digits=36, decimal_places=10)
    source_allocation_contract_id = models.CharField(max_length=1024)
    ledger_active = models.BooleanField(default=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
            models.Index(fields=["claim_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.payroll_id}:{self.employee_external_id}"


class SettlementReceiptMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    amount = models.DecimalField(max_digits=36, decimal_places=10)
    settlement_reference = models.CharField(max_length=255, db_index=True)
    settlement_proof = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
            models.Index(fields=["settlement_reference"]),
        ]

    def __str__(self) -> str:
        return self.settlement_reference


class PayslipMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    salary_breakdown = models.JSONField(default=dict, blank=True)
    settlement_proof = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.payroll_id}:{self.employee_external_id}"


class SettledSalaryRecordMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    amount = models.DecimalField(max_digits=36, decimal_places=10)
    settlement_reference = models.CharField(max_length=255, db_index=True)
    settlement_proof = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
            models.Index(fields=["settlement_reference"]),
        ]

    def __str__(self) -> str:
        return self.settlement_reference


class FailedSalaryClaimMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    hr_wallet_party = models.CharField(max_length=512, db_index=True)
    company_admin_party = models.CharField(max_length=512, db_index=True)
    amount = models.DecimalField(max_digits=36, decimal_places=10)
    failure_reason = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.company_id}:{self.payroll_id}:{self.employee_external_id}"


class SettlementProofMirror(models.Model):
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    employee_wallet_party = models.CharField(max_length=512, db_index=True)
    employer_wallet_party = models.CharField(max_length=512, db_index=True)
    amount = models.DecimalField(max_digits=36, decimal_places=10)
    settlement_reference = models.CharField(max_length=255, db_index=True)
    proof_payload = models.JSONField(default=dict, blank=True)
    ledger_command = models.ForeignKey(
        LedgerCommand,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="settlement_proofs",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
            models.Index(fields=["settlement_reference"]),
        ]

    def __str__(self) -> str:
        return self.settlement_reference


class USDCxTransferRecord(models.Model):
    company_id = models.CharField(max_length=255, db_index=True)
    payroll_id = models.CharField(max_length=255, db_index=True)
    employee_external_id = models.CharField(max_length=255, db_index=True)
    salary_claim_contract_id = models.CharField(max_length=1024, db_index=True)
    settlement_reference = models.CharField(max_length=255, db_index=True)
    provider_name = models.CharField(max_length=128, blank=True, db_index=True)
    provider_status = models.CharField(max_length=32, db_index=True)
    sender_party = models.CharField(max_length=512, db_index=True)
    receiver_party = models.CharField(max_length=512, db_index=True)
    amount = models.DecimalField(max_digits=36, decimal_places=10)
    token = models.JSONField(default=dict, blank=True)
    transfer_instruction_cid = models.CharField(max_length=1024, blank=True)
    holding_cid = models.CharField(max_length=1024, blank=True)
    raw_provider_reference = models.CharField(max_length=1024, blank=True)
    proof_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    ledger_command = models.ForeignKey(
        LedgerCommand,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="usdcx_transfer_records",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id", "payroll_id", "employee_external_id"]),
            models.Index(fields=["salary_claim_contract_id", "settlement_reference"]),
            models.Index(fields=["provider_name", "provider_status"]),
        ]

    def __str__(self) -> str:
        return f"{self.provider_name}:{self.settlement_reference}:{self.provider_status}"


class ZUSDHoldingMirror(models.Model):
    contract_id = models.CharField(max_length=1024, unique=True)
    issuer_party = models.CharField(max_length=512, db_index=True)
    owner_party = models.CharField(max_length=512, db_index=True)
    amount = models.DecimalField(max_digits=36, decimal_places=10)
    symbol = models.CharField(max_length=16, default="ZUSD", db_index=True)
    reference = models.CharField(max_length=255, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    ledger_active = models.BooleanField(default=True, db_index=True)
    ledger_offset = models.CharField(max_length=255, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner_party", "symbol", "ledger_active"]),
            models.Index(fields=["issuer_party", "reference"]),
        ]

    def __str__(self) -> str:
        return f"{self.symbol}:{self.owner_party}:{self.amount}"


class ZUSDFaucetRequest(models.Model):
    request_id = models.CharField(max_length=255, unique=True)
    owner_party = models.CharField(max_length=512, db_index=True)
    issuer_party = models.CharField(max_length=512, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=36, decimal_places=10)
    symbol = models.CharField(max_length=16, default="ZUSD")
    reference = models.CharField(max_length=255, db_index=True)
    environment = models.CharField(max_length=64, default="sandbox", db_index=True)
    status = models.CharField(
        max_length=32,
        choices=FaucetRequestStatus.choices,
        default=FaucetRequestStatus.REQUESTED,
        db_index=True,
    )
    holding_contract_id = models.CharField(max_length=1024, blank=True)
    grant_contract_id = models.CharField(max_length=1024, blank=True)
    update_id = models.CharField(max_length=255, blank=True, db_index=True)
    failure_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ledger_command = models.ForeignKey(
        LedgerCommand,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="zusd_faucet_requests",
    )
    minted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner_party", "environment", "status", "created_at"]),
            models.Index(fields=["reference", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.request_id}:{self.status}"
