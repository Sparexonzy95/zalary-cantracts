import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.roles import register_ledger_party


class Command(BaseCommand):
    help = "Register a local LedgerParty role mapping for Zalary backend authorization."

    def add_arguments(self, parser):
        parser.add_argument("--party-id", required=True)
        parser.add_argument("--role", required=True)
        parser.add_argument("--display-name", default="")

    def handle(self, *args, **options):
        try:
            party = register_ledger_party(
                party_id=options["party_id"],
                role=options["role"],
                display_name=options.get("display_name"),
            )
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("Ledger party registration failed.") from exc

        self.stdout.write(
            json.dumps(
                {
                    "status": "ok",
                    "party_id": party.party_id,
                    "role": party.role,
                    "role_label": party.get_role_display() if party.role else "",
                    "display_name": party.display_name,
                    "is_active": party.is_active,
                },
                indent=2,
                sort_keys=True,
            )
        )
