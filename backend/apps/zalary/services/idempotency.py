import hashlib
import json
from dataclasses import dataclass
from typing import Any

from apps.zalary.models import CommandStatus, LedgerCommand


PENDING_STATUSES = {CommandStatus.PENDING, CommandStatus.SUBMITTED}


@dataclass(frozen=True)
class IdempotencyMatch:
    status: str
    command: LedgerCommand

    def safe_summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "command_id": self.command.command_id,
            "update_id": self.command.update_id,
            "ledger_command_id": self.command.pk,
            "workflow_id": self.command.workflow_id,
        }


def build_idempotency_key(action: str, **parts: Any) -> str:
    normalized = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    clean_action = action.strip().lower().replace("_", "-")
    return f"zalary-{clean_action}-{digest}"


def key_create_company(*, platform_config_contract_id: str, company_id: str) -> str:
    return build_idempotency_key("create-company", platform_config_contract_id=platform_config_contract_id, company_id=company_id)


def key_enroll_employee(*, company_id: str, employee_external_id: str) -> str:
    return build_idempotency_key("enroll-employee", company_id=company_id, employee_external_id=employee_external_id)


def key_create_payroll_vault(*, company_id: str, payroll_id: str) -> str:
    return build_idempotency_key("create-payroll-vault", company_id=company_id, payroll_id=payroll_id)


def key_add_salary_allocation(*, company_id: str, payroll_id: str, employee_external_id: str) -> str:
    return build_idempotency_key(
        "add-salary-allocation",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )


def key_finalize_allocation(*, company_id: str, payroll_id: str) -> str:
    return build_idempotency_key("finalize-allocation", company_id=company_id, payroll_id=payroll_id)


def key_confirm_funding(*, company_id: str, payroll_id: str, funding_reference: str) -> str:
    return build_idempotency_key(
        "confirm-funding",
        company_id=company_id,
        payroll_id=payroll_id,
        funding_reference=funding_reference,
    )


def key_activate_payroll(*, company_id: str, payroll_id: str) -> str:
    return build_idempotency_key("activate-payroll", company_id=company_id, payroll_id=payroll_id)


def key_issue_claim_ticket(*, company_id: str, payroll_id: str, employee_external_id: str) -> str:
    return build_idempotency_key(
        "issue-claim-ticket",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )


def key_request_salary_claim(*, company_id: str, payroll_id: str, employee_external_id: str) -> str:
    return build_idempotency_key(
        "request-salary-claim",
        company_id=company_id,
        payroll_id=payroll_id,
        employee_external_id=employee_external_id,
    )


def key_confirm_settlement(*, salary_claim_contract_id: str, settlement_reference: str) -> str:
    return build_idempotency_key(
        "confirm-settlement",
        salary_claim_contract_id=salary_claim_contract_id,
        settlement_reference=settlement_reference,
    )


def find_existing_command(*, workflow_id: str, template_id: str, choice_name: str) -> IdempotencyMatch | None:
    queryset = LedgerCommand.objects.filter(
        workflow_id=workflow_id,
        template_id=template_id,
        choice_name=choice_name,
    ).order_by("-created_at")
    succeeded = queryset.filter(status=CommandStatus.SUCCEEDED).first()
    if succeeded is not None:
        return IdempotencyMatch(status="exists", command=succeeded)
    pending = queryset.filter(status__in=PENDING_STATUSES).first()
    if pending is not None:
        return IdempotencyMatch(status="pending", command=pending)
    return None
