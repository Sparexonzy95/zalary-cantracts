from typing import Any


def action_success_envelope(result, *, resource: dict[str, Any] | None = None, next_actions: list[str] | None = None) -> dict[str, Any]:
    summary = result.safe_summary() if hasattr(result, "safe_summary") else dict(result)
    return {
        "status": summary.get("status", "ok"),
        "action": summary.get("action", ""),
        "resource": resource or _resource_from_summary(summary),
        "ledger": {
            "command_id": summary.get("command_id", ""),
            "update_id": summary.get("update_id"),
            "ledger_command_id": summary.get("ledger_command_id"),
        },
        "transfer": summary.get("transfer") or {
            "provider": "",
            "status": "",
            "transfer_instruction_cid": "",
            "holding_cid": "",
        },
        "sync": _sync_from_summary(summary),
        "next_actions": next_actions or next_actions_for_summary(summary),
    }


def error_envelope(*, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "error",
        "code": code,
        "message": message,
        "details": details or {},
    }


def next_actions_for_summary(summary: dict[str, Any]) -> list[str]:
    status = summary.get("status")
    action = summary.get("action")
    if status == "pending":
        return ["no_action_available"]
    if action == "RequestSalaryClaim" and status in {"ok", "exists"}:
        return ["confirm_settlement"]
    if action == "ConfirmSalarySettlement" and status in {"ok", "exists"}:
        return ["view_payslip"]
    return ["no_action_available"]


def _resource_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "contract_id",
        "company_id",
        "payroll_id",
        "employee_external_id",
        "salary_claim_contract_id",
        "payroll_vault_contract_id",
        "settlement_reference",
        "transfer_record_id",
        "existing_contract",
    )
    return {key: summary[key] for key in keys if key in summary}


def _sync_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in summary.items()
        if key.startswith("synced_") or key == "final_sync"
    }
