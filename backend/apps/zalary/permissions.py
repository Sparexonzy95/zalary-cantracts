from rest_framework.permissions import BasePermission

from .models import LedgerRole


def _request_roles(request) -> set[str]:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return set()

    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return {role.value for role in LedgerRole}

    roles = getattr(user, "zalary_roles", None)
    if roles is None:
        roles = getattr(user, "roles", [])

    return {str(role) for role in roles}


class HasZalaryRole(BasePermission):
    required_role: str | None = None

    def has_permission(self, request, view) -> bool:
        if self.required_role is None:
            return False
        return self.required_role in _request_roles(request)


class IsPlatformAdmin(HasZalaryRole):
    required_role = LedgerRole.PLATFORM_ADMIN


class IsCompanyAdmin(HasZalaryRole):
    required_role = LedgerRole.COMPANY_ADMIN


class IsHRWallet(HasZalaryRole):
    required_role = LedgerRole.HR


class IsEmployerWallet(HasZalaryRole):
    required_role = LedgerRole.EMPLOYER


class IsEmployeeWallet(HasZalaryRole):
    required_role = LedgerRole.EMPLOYEE
