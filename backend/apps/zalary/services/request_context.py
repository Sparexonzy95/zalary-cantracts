from typing import Iterable

from .auth import env_flag_enabled
from .errors import AuthBindingError


ALLOW_UNBOUND_DEMO_AUTH = "ZALARY_ALLOW_UNBOUND_DEMO_AUTH"


def get_authenticated_party_ids(request) -> list[str]:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return []

    for attr in ("zalary_party_ids", "ledger_party_ids", "party_ids"):
        values = getattr(user, attr, None)
        parties = _normalize_parties(values)
        if parties:
            return parties

    profile = getattr(user, "profile", None)
    if profile is not None:
        for attr in ("zalary_party_ids", "ledger_party_ids", "party_ids", "party_id"):
            parties = _normalize_parties(getattr(profile, attr, None))
            if parties:
                return parties

    return []


def require_party_for_role(request, role: str, allowed_parties: Iterable[str]) -> str:
    parties = get_authenticated_party_ids(request)
    allowed = [party for party in allowed_parties if party]
    for party in parties:
        if party in allowed:
            return party

    if _unbound_demo_auth_enabled():
        for party in allowed:
            return party

    raise AuthBindingError(f"Authenticated user is not bound to a permitted {role} ledger party.")


def derive_act_as_from_request(request, expected_party: str) -> list[str]:
    party = require_party_for_role(request, "actAs", [expected_party])
    return [party]


def _unbound_demo_auth_enabled() -> bool:
    return env_flag_enabled(ALLOW_UNBOUND_DEMO_AUTH, default=False)


def _normalize_parties(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, (list, tuple, set)):
        return []
    parties = []
    seen = set()
    for value in values:
        party = str(value or "").strip()
        if not party or party in seen:
            continue
        seen.add(party)
        parties.append(party)
    return parties
