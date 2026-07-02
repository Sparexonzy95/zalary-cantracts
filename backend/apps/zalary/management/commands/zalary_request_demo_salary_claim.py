import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.settlement import request_salary_claim


class Command(BaseCommand):
    help = "Exercise ClaimTicket.RequestSalaryClaim for a mirrored demo claim ticket."

    def add_arguments(self, parser):
        parser.add_argument("--claim-ticket-contract-id")
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")
        parser.add_argument("--employee-external-id", default="EMP-001")
        parser.add_argument(
            "--allow-existing",
            action="store_true",
            help="Return the existing SalaryClaim instead of submitting a duplicate command.",
        )

    def handle(self, *args, **options):
        try:
            result = request_salary_claim(
                claim_ticket_contract_id=options.get("claim_ticket_contract_id"),
                company_id=options.get("company_id"),
                payroll_id=options.get("payroll_id"),
                employee_external_id=options.get("employee_external_id"),
                allow_existing=options["allow_existing"],
                sync_after=True,
            )
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": options.get("company_id") or "",
                        "payroll_id": options.get("payroll_id") or "",
                        "employee_external_id": options.get("employee_external_id") or "",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("Demo salary claim request failed.") from exc

        self.stdout.write(json.dumps(result.safe_summary(), indent=2, sort_keys=True))
