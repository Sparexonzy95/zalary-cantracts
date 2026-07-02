from dataclasses import dataclass, field
from typing import Any, Protocol

from apps.zalary.services.errors import SettlementProofError
from apps.zalary.services.payloads import decimal_to_daml


TRANSFER_COMPLETED = "completed"
TRANSFER_PENDING = "pending"
TRANSFER_PENDING_RECEIVER_ACCEPTANCE = "pending_receiver_acceptance"
TRANSFER_FAILED = "failed"
TRANSFER_UNAVAILABLE = "unavailable"
FINAL_TRANSFER_STATUSES = {
    TRANSFER_COMPLETED,
    TRANSFER_PENDING,
    TRANSFER_PENDING_RECEIVER_ACCEPTANCE,
    TRANSFER_FAILED,
    TRANSFER_UNAVAILABLE,
}


@dataclass(frozen=True)
class TokenTransferRequest:
    company_id: str
    payroll_id: str
    employee_external_id: str
    salary_claim_contract_id: str
    token: dict[str, Any]
    sender_party: str
    receiver_party: str
    amount: str
    transfer_reference: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenTransferResult:
    status: str
    token: dict[str, Any] = field(default_factory=dict)
    sender: str = ""
    receiver: str = ""
    amount: str = ""
    transferReference: str = ""
    transferInstructionCid: str | None = None
    holdingCid: str | None = None
    executedAt: str = ""
    provider_name: str = ""
    raw_provider_reference: str = ""
    error_message: str = ""
    proof_payload: dict[str, Any] = field(default_factory=dict)

    def safe_summary(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "status": self.status,
            "transfer_instruction_cid": self.transferInstructionCid or "",
            "holding_cid": self.holdingCid or "",
            "raw_provider_reference": self.raw_provider_reference,
            "error_message": self.error_message,
        }


class TokenTransferProvider(Protocol):
    provider_name: str

    def execute_transfer(self, request: TokenTransferRequest) -> TokenTransferResult:
        ...

    def build_token_transfer_proof(self, result: TokenTransferResult) -> dict[str, Any]:
        ...


class BaseTokenTransferProvider:
    provider_name = "base"

    def execute_transfer(self, request: TokenTransferRequest) -> TokenTransferResult:
        return TokenTransferResult(
            status=TRANSFER_UNAVAILABLE,
            provider_name=self.provider_name,
            error_message="Token transfer provider is not configured; real settlement cannot be confirmed.",
        )

    def build_token_transfer_proof(self, result: TokenTransferResult) -> dict[str, Any]:
        if result.status != TRANSFER_COMPLETED:
            raise SettlementProofError("TokenTransferProof can only be built from a completed transfer result.")
        if result.proof_payload:
            return dict(result.proof_payload)
        if not all(
            [
                result.token,
                result.sender,
                result.receiver,
                result.amount,
                result.transferReference,
                result.executedAt,
            ]
        ):
            raise SettlementProofError("Completed transfer result is missing proof fields.")
        return {
            "token": result.token,
            "sender": result.sender,
            "receiver": result.receiver,
            "amount": decimal_to_daml(result.amount),
            "transferReference": result.transferReference,
            "transferInstructionCid": result.transferInstructionCid,
            "holdingCid": result.holdingCid,
            "executedAt": result.executedAt,
        }


class UnavailableTokenTransferProvider(BaseTokenTransferProvider):
    provider_name = "unavailable"

    def execute_transfer(self, request: TokenTransferRequest) -> TokenTransferResult:
        return TokenTransferResult(
            status=TRANSFER_UNAVAILABLE,
            token=request.token,
            sender=request.sender_party,
            receiver=request.receiver_party,
            amount=request.amount,
            transferReference=request.transfer_reference,
            provider_name=self.provider_name,
            error_message="No token transfer provider is configured; real settlement cannot be confirmed.",
        )
