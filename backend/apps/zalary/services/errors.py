class ZalaryBackendError(Exception):
    """Base error for backend integration failures."""


class ConfigurationError(ZalaryBackendError):
    """Raised when required backend configuration is missing or invalid."""


class LedgerNotImplementedError(ZalaryBackendError, NotImplementedError):
    """Raised by scaffolded Ledger API methods that are intentionally empty."""


class LedgerSubmissionError(ZalaryBackendError):
    """Raised when a future Ledger API command submission fails."""


class LedgerSyncError(ZalaryBackendError):
    """Raised when a future ledger sync operation cannot complete."""


class AuthBindingError(ZalaryBackendError):
    """Raised when an authenticated request is not bound to the required ledger party."""


class IdempotentCommandPendingError(ZalaryBackendError):
    """Raised when an equivalent command is already pending or submitted."""


class DuplicateCompanyError(ZalaryBackendError):
    """Raised when a company already exists in the local mirror."""


class DuplicateEnrollmentError(ZalaryBackendError):
    """Raised when an employee enrollment already exists in the local mirror."""


class DuplicatePayrollVaultError(ZalaryBackendError):
    """Raised when a payroll vault already exists in the local mirror."""


class DuplicateSalaryAllocationError(ZalaryBackendError):
    """Raised when a salary allocation already exists in the local mirror."""


class DuplicateClaimTicketError(ZalaryBackendError):
    """Raised when a claim ticket already exists in the local mirror."""


class DuplicateSalaryClaimError(ZalaryBackendError):
    """Raised when a salary claim already exists in the local mirror."""


class DuplicateSettlementError(ZalaryBackendError):
    """Raised when a salary settlement already exists in the local mirror."""


class OnboardingValidationError(ZalaryBackendError):
    """Raised when onboarding input fails local backend validation."""


class SettlementProofError(ZalaryBackendError):
    """Raised when settlement proof data is missing or invalid."""


def safe_error_message(error: Exception) -> str:
    """Return a caller-safe error string that never includes credentials."""
    message = str(error).strip()
    if not message:
        return error.__class__.__name__
    return message
