from apps.zalary.models import LedgerParty, LedgerRole

from .errors import OnboardingValidationError


def register_ledger_party(
    *,
    party_id: str,
    role: str,
    display_name: str | None = None,
) -> LedgerParty:
    cleaned_party_id = _require_party_id(party_id)
    cleaned_role = _require_role(role)
    cleaned_display_name = (display_name or "").strip()

    party, _created = LedgerParty.objects.update_or_create(
        party_id=cleaned_party_id,
        defaults={
            "role": cleaned_role,
            "display_name": cleaned_display_name,
            "is_active": True,
        },
    )
    return party


def get_parties_by_role(role: str):
    cleaned_role = _require_role(role)
    return LedgerParty.objects.filter(role=cleaned_role, is_active=True).order_by("party_id")


def ensure_party_registered(*, party_id: str, expected_role: str | None = None) -> LedgerParty:
    cleaned_party_id = _require_party_id(party_id)
    try:
        party = LedgerParty.objects.get(party_id=cleaned_party_id, is_active=True)
    except LedgerParty.DoesNotExist as exc:
        raise OnboardingValidationError(f"Ledger party is not registered: {cleaned_party_id}.") from exc

    if expected_role:
        cleaned_role = _require_role(expected_role)
        if party.role != cleaned_role:
            raise OnboardingValidationError(
                f"Ledger party {cleaned_party_id} is registered as {party.role or 'unassigned'}, not {cleaned_role}."
            )
    return party


def company_role_summary(company) -> dict:
    parties = _dedupe_preserving_order(
        [
            company.company_admin_party,
            *company.admin_wallet_parties,
            *company.hr_wallet_parties,
            *company.employer_wallet_parties,
        ]
    )
    registry = {party.party_id: party for party in LedgerParty.objects.filter(party_id__in=parties)}

    return {
        "company_id": company.company_id,
        "company_name": company.company_name,
        "company_admin_party": company.company_admin_party,
        "admin_wallet_parties": company.admin_wallet_parties,
        "hr_wallet_parties": company.hr_wallet_parties,
        "employer_wallet_parties": company.employer_wallet_parties,
        "allowed_tokens": company.allowed_tokens,
        "party_registry": [
            _party_registry_entry(party_id=party_id, party=registry.get(party_id))
            for party_id in parties
        ],
    }


def _party_registry_entry(*, party_id: str, party: LedgerParty | None) -> dict:
    if party is None:
        return {
            "party_id": party_id,
            "registered": False,
            "role": "",
            "role_label": "",
            "display_name": "",
        }
    return {
        "party_id": party_id,
        "registered": party.is_active,
        "role": party.role,
        "role_label": party.get_role_display() if party.role else "",
        "display_name": party.display_name,
    }


def _require_party_id(party_id: str) -> str:
    cleaned = (party_id or "").strip()
    if not cleaned:
        raise OnboardingValidationError("party_id is required.")
    return cleaned


def _require_role(role: str) -> str:
    cleaned = (role or "").strip()
    allowed_roles = {choice.value for choice in LedgerRole}
    if cleaned not in allowed_roles:
        allowed = ", ".join(sorted(allowed_roles))
        raise OnboardingValidationError(f"Invalid ledger role '{cleaned}'. Allowed roles: {allowed}.")
    return cleaned


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped
