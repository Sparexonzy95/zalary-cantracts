import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.zalary.services.commands import create_company
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message


class Command(BaseCommand):
    help = "Create a demo Zalary company through ZalaryConfig.CreateCompany."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", dest="company_id")
        parser.add_argument("--company-name", dest="company_name", default="Zalary Demo Company")
        parser.add_argument("--company-admin", dest="company_admin")
        parser.add_argument(
            "--allow-existing",
            action="store_true",
            help="Return the existing mirrored company instead of submitting a duplicate create command.",
        )

    def handle(self, *args, **options):
        company_id = options.get("company_id") or _generated_company_id()
        company_name = options["company_name"]
        company_admin = options.get("company_admin")

        try:
            result = create_company(
                company_id=company_id,
                company_name=company_name,
                company_admin=company_admin,
                sync_after=True,
                allow_single_party_demo=True,
                allow_existing=options["allow_existing"],
            )
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": company_id,
                        "company_name": company_name,
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("Demo company creation failed.") from exc

        self.stdout.write(json.dumps(result.safe_summary(), indent=2, sort_keys=True))


def _generated_company_id() -> str:
    return f"zalary-demo-{timezone.now().strftime('%Y%m%d-%H%M%S')}"
