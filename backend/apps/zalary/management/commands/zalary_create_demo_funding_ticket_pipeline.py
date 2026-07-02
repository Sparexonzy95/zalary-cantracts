import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.payroll import create_demo_funding_activation_ticket_pipeline


class Command(BaseCommand):
    help = "Confirm funding, activate payroll, and issue a demo claim ticket."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", required=True)
        parser.add_argument("--payroll-id", required=True)
        parser.add_argument("--employee-external-id", default="EMP-001")
        parser.add_argument("--funding-amount")
        parser.add_argument("--funding-reference")
        parser.add_argument(
            "--allow-existing",
            action="store_true",
            help="Reuse already completed funding, activation, or claim-ticket steps.",
        )

    def handle(self, *args, **options):
        try:
            result = create_demo_funding_activation_ticket_pipeline(
                company_id=options["company_id"],
                payroll_id=options["payroll_id"],
                employee_external_id=options["employee_external_id"],
                funding_amount=options.get("funding_amount"),
                funding_reference=options.get("funding_reference"),
                allow_existing=options["allow_existing"],
            )
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": options["company_id"],
                        "payroll_id": options["payroll_id"],
                        "employee_external_id": options["employee_external_id"],
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("Demo funding-ticket pipeline failed.") from exc

        self.stdout.write(json.dumps(result.safe_summary(), indent=2, sort_keys=True))
