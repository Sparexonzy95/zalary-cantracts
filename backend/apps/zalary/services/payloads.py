from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any


TOKEN_FIELDS = ("symbol", "instrumentId", "instrumentAdmin", "utilityApiUrl", "xReserveApiUrl")
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def decimal_to_daml(value: Decimal | str | int | float) -> str:
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Decimal value is invalid.") from exc
    if decimal.as_tuple().exponent < -10:
        raise ValueError("Decimal value cannot have more than 10 decimal places.")
    return format(decimal.quantize(Decimal("0.0000000000")), "f")


def optional_text(value: str | None) -> dict[str, Any]:
    if value:
        return {"tag": "Some", "value": value}
    return {"tag": "None", "value": {}}


def token_instrument_payload(
    *,
    symbol: str,
    instrument_id: str,
    instrument_admin: str,
    utility_api_url: str,
    xreserve_api_url: str,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "instrumentId": instrument_id,
        "instrumentAdmin": instrument_admin,
        "utilityApiUrl": utility_api_url,
        "xReserveApiUrl": xreserve_api_url,
    }


def payroll_period_payload(*, label: str, starts_at: str, ends_at: str) -> dict[str, str]:
    return {
        "label": label,
        "startsAt": starts_at,
        "endsAt": ends_at,
    }


def salary_breakdown_payload(
    *,
    gross_pay: Decimal | str,
    allowances: Decimal | str,
    deductions: Decimal | str,
    net_pay: Decimal | str,
    token: dict[str, Any],
) -> dict[str, Any]:
    return {
        "grossPay": decimal_to_daml(gross_pay),
        "allowances": decimal_to_daml(allowances),
        "deductions": decimal_to_daml(deductions),
        "netPay": decimal_to_daml(net_pay),
        "token": token,
    }


def token_transfer_proof_payload(
    *,
    token: dict[str, Any],
    sender: str,
    receiver: str,
    amount: Decimal | str,
    transfer_reference: str,
    executed_at: str,
    transfer_instruction_cid: str | None = None,
    holding_cid: str | None = None,
) -> dict[str, Any]:
    return {
        "token": token,
        "sender": sender,
        "receiver": receiver,
        "amount": decimal_to_daml(amount),
        "transferReference": transfer_reference,
        "transferInstructionCid": optional_text(transfer_instruction_cid),
        "holdingCid": optional_text(holding_cid),
        "executedAt": executed_at,
    }


def create_company_choice_payload(**kwargs: Any) -> dict[str, Any]:
    allowed_tokens = kwargs.get("allowedTokens") or kwargs.get("allowed_tokens")
    if not allowed_tokens:
        raise ValueError("allowedTokens must include at least one TokenInstrument.")

    company_admin = _required_text(kwargs.get("companyAdmin") or kwargs.get("company_admin"), "companyAdmin")
    company_id = _required_company_id(kwargs.get("companyId") or kwargs.get("company_id"))
    admin_wallets = _unique_party_list(
        kwargs.get("adminWallets") or kwargs.get("admin_wallets"),
        "adminWallets",
        allow_empty=False,
    )
    if company_admin not in admin_wallets:
        raise ValueError("companyAdmin must be included in adminWallets.")

    return {
        "companyAdmin": company_admin,
        "companyName": _required_text(kwargs.get("companyName") or kwargs.get("company_name"), "companyName"),
        "companyId": company_id,
        "adminWallets": admin_wallets,
        "hrWallets": _unique_party_list(kwargs.get("hrWallets") or kwargs.get("hr_wallets"), "hrWallets"),
        "employerWallets": _unique_party_list(
            kwargs.get("employerWallets") or kwargs.get("employer_wallets"),
            "employerWallets",
        ),
        "allowedTokens": [_normalize_token_instrument(token) for token in allowed_tokens],
    }


def create_employee_enrollment_choice_payload(**kwargs: Any) -> dict[str, Any]:
    return {
        "hrWallet": _required_text(kwargs.get("hrWallet") or kwargs.get("hr_wallet"), "hrWallet"),
        "employerWallet": _required_text(
            kwargs.get("employerWallet") or kwargs.get("employer_wallet"),
            "employerWallet",
        ),
        "employeeWallet": _required_text(
            kwargs.get("employeeWallet") or kwargs.get("employee_wallet"),
            "employeeWallet",
        ),
        "employeeExternalId": _required_safe_text(
            kwargs.get("employeeExternalId") or kwargs.get("employee_external_id"),
            "employeeExternalId",
        ),
    }


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required.")
    return value.strip()


def _required_safe_text(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not SAFE_ID_PATTERN.fullmatch(text):
        raise ValueError(f"{field_name} must be safe text using letters, numbers, dots, dashes, or underscores.")
    return text


def _required_company_id(value: Any) -> str:
    company_id = _required_text(value, "companyId")
    if not SAFE_ID_PATTERN.fullmatch(company_id):
        raise ValueError("companyId must be slug-like text using letters, numbers, dots, dashes, or underscores.")
    return company_id


def _unique_party_list(value: Any, field_name: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        if allow_empty:
            return []
        raise ValueError(f"{field_name} must be a non-empty list.")

    parties = _dedupe_preserving_order([_required_text(item, field_name) for item in value])
    if not parties and not allow_empty:
        raise ValueError(f"{field_name} must be a non-empty list.")
    return parties


def _normalize_token_instrument(token: Any) -> dict[str, str]:
    if not isinstance(token, dict):
        raise ValueError("allowedTokens entries must be TokenInstrument objects.")

    normalized = {field: _required_text(token.get(field), field) for field in TOKEN_FIELDS}
    return normalized


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def create_payroll_vault_choice_payload(**kwargs: Any) -> dict[str, Any]:
    return {
        "hrWallet": _required_text(kwargs.get("hrWallet") or kwargs.get("hr_wallet"), "hrWallet"),
        "employerWallet": _required_text(
            kwargs.get("employerWallet") or kwargs.get("employer_wallet"),
            "employerWallet",
        ),
        "payrollId": _required_safe_text(kwargs.get("payrollId") or kwargs.get("payroll_id"), "payrollId"),
        "payrollPeriod": _normalize_payroll_period(
            kwargs.get("payrollPeriod") or kwargs.get("payroll_period"),
        ),
        "payrollToken": _normalize_token_instrument(
            kwargs.get("payrollToken") or kwargs.get("payroll_token"),
        ),
        "claimWindowStart": _timestamp_to_daml(
            kwargs.get("claimWindowStart") or kwargs.get("claim_window_start"),
            "claimWindowStart",
        ),
        "claimWindowEnd": _timestamp_to_daml(
            kwargs.get("claimWindowEnd") or kwargs.get("claim_window_end"),
            "claimWindowEnd",
        ),
        "expectedEmployeeCount": _positive_int_text(
            kwargs.get("expectedEmployeeCount") or kwargs.get("expected_employee_count"),
            "expectedEmployeeCount",
        ),
    }


def add_salary_allocation_choice_payload(**kwargs: Any) -> dict[str, Any]:
    return {
        "allocationEmployeeWallet": _required_text(
            kwargs.get("allocationEmployeeWallet") or kwargs.get("allocation_employee_wallet"),
            "allocationEmployeeWallet",
        ),
        "employeeExternalId": _required_safe_text(
            kwargs.get("employeeExternalId") or kwargs.get("employee_external_id"),
            "employeeExternalId",
        ),
        "salaryBreakdown": _normalize_salary_breakdown(
            kwargs.get("salaryBreakdown") or kwargs.get("salary_breakdown"),
        ),
        "enrollmentCid": _required_text(kwargs.get("enrollmentCid") or kwargs.get("enrollment_cid"), "enrollmentCid"),
    }


def finalize_allocations_choice_payload(**kwargs: Any) -> dict[str, Any]:
    return {}


def confirm_funding_choice_payload(**kwargs: Any) -> dict[str, Any]:
    funding_proof = kwargs.get("fundingProof") if "fundingProof" in kwargs else kwargs.get("funding_proof")
    return {
        "fundingAmount": decimal_to_daml(kwargs.get("fundingAmount") or kwargs.get("funding_amount")),
        "fundingReference": _required_text(
            kwargs.get("fundingReference") or kwargs.get("funding_reference"),
            "fundingReference",
        ),
        "fundingProof": _normalize_optional_transfer_proof(funding_proof),
    }


def activate_payroll_choice_payload(**kwargs: Any) -> dict[str, Any]:
    return {}


def issue_claim_ticket_choice_payload(**kwargs: Any) -> dict[str, Any]:
    return {
        "payrollVaultCid": _required_text(
            kwargs.get("payrollVaultCid") or kwargs.get("payroll_vault_cid"),
            "payrollVaultCid",
        )
    }


def request_salary_claim_choice_payload(**kwargs: Any) -> dict[str, Any]:
    return {}


def confirm_salary_settlement_choice_payload(**kwargs: Any) -> dict[str, Any]:
    settlement_proof = (
        kwargs.get("settlementProof")
        if "settlementProof" in kwargs
        else kwargs.get("settlement_proof")
    )
    return {
        "payrollVaultCid": _required_text(
            kwargs.get("payrollVaultCid") or kwargs.get("payroll_vault_cid"),
            "payrollVaultCid",
        ),
        "settlementReference": _required_text(
            kwargs.get("settlementReference") or kwargs.get("settlement_reference"),
            "settlementReference",
        ),
        "settlementProof": _normalize_transfer_proof(settlement_proof, "settlementProof"),
    }


def _normalize_payroll_period(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("payrollPeriod must be an object.")
    return {
        "label": _required_text(value.get("label"), "payrollPeriod.label"),
        "startsAt": _date_to_daml(value.get("startsAt"), "payrollPeriod.startsAt"),
        "endsAt": _date_to_daml(value.get("endsAt"), "payrollPeriod.endsAt"),
    }


def _normalize_salary_breakdown(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("salaryBreakdown must be an object.")
    return {
        "grossPay": decimal_to_daml(value.get("grossPay")),
        "allowances": decimal_to_daml(value.get("allowances")),
        "deductions": decimal_to_daml(value.get("deductions")),
        "netPay": decimal_to_daml(value.get("netPay")),
        "token": _normalize_token_instrument(value.get("token")),
    }


def _normalize_optional_transfer_proof(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("fundingProof must be an object or null.")
    return _normalize_transfer_proof(value, "fundingProof")


def _normalize_transfer_proof(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object.")
    return {
        "token": _normalize_token_instrument(value.get("token")),
        "sender": _required_text(value.get("sender"), f"{field_name}.sender"),
        "receiver": _required_text(value.get("receiver"), f"{field_name}.receiver"),
        "amount": decimal_to_daml(value.get("amount")),
        "transferReference": _required_text(value.get("transferReference"), f"{field_name}.transferReference"),
        "transferInstructionCid": _optional_text_input(value.get("transferInstructionCid")),
        "holdingCid": _optional_text_input(value.get("holdingCid")),
        "executedAt": _timestamp_to_daml(value.get("executedAt"), f"{field_name}.executedAt"),
    }


def _date_to_daml(value: Any, field_name: str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _required_text(value, field_name)


def _timestamp_to_daml(value: Any, field_name: str) -> str:
    if isinstance(value, datetime):
        timestamp = value.isoformat()
        return timestamp.replace("+00:00", "Z")
    return _required_text(value, field_name)


def _optional_text_input(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _required_text(value, "optional text")


def _positive_int_text(value: Any, field_name: str) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer.") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be a positive integer.")
    return str(number)
