from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from django.utils import timezone

from apps.zalary.models import CommandStatus, CompanyMirror, EmployeeEnrollmentMirror, LedgerCommand

from .auth import default_read_parties, load_ledger_auth_settings
from .errors import DuplicateEnrollmentError, LedgerSubmissionError, OnboardingValidationError, safe_error_message
from .ledger import CommandContext, LedgerClient
from .payloads import create_employee_enrollment_choice_payload
from .sync import EmployeeEnrollmentSyncResult, sync_employee_enrollments
from .templates import CHOICES, COMPANY


@dataclass(frozen=True)
class EmployeeEnrollmentPreflightResult:
    status: str
    company_id: str
    company_name: str
    employee_external_id: str
    hr_wallet: str
    employer_wallet: str
    employee_wallet: str
    act_as: list[str]
    future_command: dict

    def safe_summary(self) -> dict:
        return {
            "status": self.status,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "employee_external_id": self.employee_external_id,
            "hr_wallet": self.hr_wallet,
            "employer_wallet": self.employer_wallet,
            "employee_wallet": self.employee_wallet,
            "act_as": self.act_as,
            "future_command": self.future_command,
        }


@dataclass(frozen=True)
class CreateEmployeeEnrollmentResult:
    status: str
    command_id: str
    update_id: str | None
    company_id: str
    employee_external_id: str
    hr_wallet: str
    employer_wallet: str
    employee_wallet: str
    ledger_command_pk: int
    synced_enrollments: EmployeeEnrollmentSyncResult | None = None
    sync_error: str = ""
    existing_enrollment: dict[str, Any] | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "status": self.status,
            "command_id": self.command_id,
            "update_id": self.update_id,
            "company_id": self.company_id,
            "employee_external_id": self.employee_external_id,
            "hr_wallet": self.hr_wallet,
            "employer_wallet": self.employer_wallet,
            "employee_wallet": self.employee_wallet,
            "ledger_command_id": self.ledger_command_pk,
        }
        if self.synced_enrollments is not None:
            summary["synced_enrollments"] = self.synced_enrollments.safe_summary()
        if self.existing_enrollment is not None:
            summary["existing_enrollment"] = self.existing_enrollment
        if self.sync_error:
            summary["sync_error"] = self.sync_error
        return summary


def preflight_employee_enrollment(
    *,
    company_id: str,
    hr_wallet: str,
    employer_wallet: str,
    employee_wallet: str,
    employee_external_id: str,
) -> EmployeeEnrollmentPreflightResult:
    cleaned_company_id = _required_text(company_id, "company_id")
    cleaned_hr_wallet = _required_text(hr_wallet, "hrWallet")
    cleaned_employer_wallet = _required_text(employer_wallet, "employerWallet")
    cleaned_employee_wallet = _required_text(employee_wallet, "employeeWallet")
    cleaned_employee_external_id = _required_text(employee_external_id, "employeeExternalId")

    company = CompanyMirror.objects.filter(company_id=cleaned_company_id).order_by("-synced_at").first()
    if company is None:
        raise OnboardingValidationError(f"Company not found in local mirror: {cleaned_company_id}.")

    if cleaned_hr_wallet not in (company.hr_wallet_parties or []):
        raise OnboardingValidationError("hrWallet is not authorized for this company.")
    if cleaned_employer_wallet not in (company.employer_wallet_parties or []):
        raise OnboardingValidationError("employerWallet is not authorized for this company.")
    if EmployeeEnrollmentMirror.objects.filter(
        company_id=company.company_id,
        employee_external_id=cleaned_employee_external_id,
        is_active=True,
    ).exists():
        raise OnboardingValidationError("An active employee enrollment already exists for this company/employeeExternalId.")

    return EmployeeEnrollmentPreflightResult(
        status="ok",
        company_id=company.company_id,
        company_name=company.company_name,
        employee_external_id=cleaned_employee_external_id,
        hr_wallet=cleaned_hr_wallet,
        employer_wallet=cleaned_employer_wallet,
        employee_wallet=cleaned_employee_wallet,
        act_as=[cleaned_hr_wallet],
        future_command={
            "template": COMPANY.display_id(),
            "choice": CHOICES["Company"]["CreateEmployeeEnrollment"],
            "contract_id": company.contract_id,
        },
    )


def create_employee_enrollment(
    *,
    company_id: str,
    hr_wallet: str,
    employer_wallet: str,
    employee_wallet: str,
    employee_external_id: str,
    sync_after: bool = True,
    allow_existing: bool = False,
) -> CreateEmployeeEnrollmentResult:
    existing_enrollment = _active_enrollment_for_employee(
        company_id=company_id,
        employee_external_id=employee_external_id,
    )
    if existing_enrollment is not None:
        if allow_existing:
            return CreateEmployeeEnrollmentResult(
                status="exists",
                command_id="",
                update_id=None,
                company_id=existing_enrollment.company_id,
                employee_external_id=existing_enrollment.employee_external_id,
                hr_wallet=existing_enrollment.hr_wallet_party,
                employer_wallet=existing_enrollment.employer_wallet_party,
                employee_wallet=existing_enrollment.employee_wallet_party,
                ledger_command_pk=0,
                existing_enrollment=_enrollment_summary(existing_enrollment),
            )
        raise DuplicateEnrollmentError(
            f"Employee enrollment already exists locally for {company_id}/{employee_external_id}."
        )

    preflight = preflight_employee_enrollment(
        company_id=company_id,
        hr_wallet=hr_wallet,
        employer_wallet=employer_wallet,
        employee_wallet=employee_wallet,
        employee_external_id=employee_external_id,
    )

    try:
        payload = create_employee_enrollment_choice_payload(
            hrWallet=preflight.hr_wallet,
            employerWallet=preflight.employer_wallet,
            employeeWallet=preflight.employee_wallet,
            employeeExternalId=preflight.employee_external_id,
        )
    except ValueError as exc:
        raise OnboardingValidationError(str(exc)) from exc

    read_as = default_read_parties()
    command_id = f"zalary-backend-create-enrollment-{uuid4().hex}"
    workflow_id = f"zalary-create-enrollment-{preflight.company_id}-{preflight.employee_external_id}"
    contract_id = preflight.future_command["contract_id"]
    choice = CHOICES["Company"]["CreateEmployeeEnrollment"]

    ledger_command = LedgerCommand.objects.create(
        command_id=command_id,
        workflow_id=workflow_id,
        act_as=preflight.act_as,
        read_as=read_as,
        template_id=COMPANY.display_id(),
        contract_id=contract_id,
        choice_name=choice,
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
                act_as=preflight.act_as,
                read_as=read_as,
                command_id=command_id,
                workflow_id=workflow_id,
            ),
            template=COMPANY,
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

    synced_enrollments = None
    sync_error = ""
    if sync_after:
        try:
            synced_enrollments = sync_employee_enrollments(company_id=preflight.company_id, parties=read_as)
        except Exception as exc:
            sync_error = safe_error_message(exc)

    return CreateEmployeeEnrollmentResult(
        status="ok",
        command_id=command_id,
        update_id=result.update_id,
        company_id=preflight.company_id,
        employee_external_id=preflight.employee_external_id,
        hr_wallet=preflight.hr_wallet,
        employer_wallet=preflight.employer_wallet,
        employee_wallet=preflight.employee_wallet,
        ledger_command_pk=ledger_command.pk,
        synced_enrollments=synced_enrollments,
        sync_error=sync_error,
        raw_response=result.raw_response,
    )


def _required_text(value: str | None, field_name: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise OnboardingValidationError(f"{field_name} is required.")
    return cleaned


def _active_enrollment_for_employee(*, company_id: str, employee_external_id: str) -> EmployeeEnrollmentMirror | None:
    return (
        EmployeeEnrollmentMirror.objects.filter(
            company_id=(company_id or "").strip(),
            employee_external_id=(employee_external_id or "").strip(),
            is_active=True,
        )
        .order_by("-synced_at")
        .first()
    )


def _enrollment_summary(enrollment: EmployeeEnrollmentMirror) -> dict[str, Any]:
    return {
        "contract_id": enrollment.contract_id,
        "company_id": enrollment.company_id,
        "company_admin_party": enrollment.company_admin_party,
        "hr_wallet_party": enrollment.hr_wallet_party,
        "employer_wallet_party": enrollment.employer_wallet_party,
        "employee_wallet_party": enrollment.employee_wallet_party,
        "employee_external_id": enrollment.employee_external_id,
        "is_active": enrollment.is_active,
    }
