import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.payroll import create_demo_payroll_pipeline


class Command(BaseCommand):
    help = "Create a demo payroll vault, one salary allocation, and finalize allocations."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", required=True)
        parser.add_argument("--employee-external-id", default="EMP-001")
        parser.add_argument("--payroll-id")
        parser.add_argument("--gross-pay", default="1000")
        parser.add_argument("--allowances", default="200")
        parser.add_argument("--deductions", default="100")
        parser.add_argument("--net-pay", default="1100")
        parser.add_argument(
            "--allow-existing",
            action="store_true",
            help="Reuse existing mirrored payroll setup contracts instead of submitting duplicates.",
        )

    def handle(self, *args, **options):
        try:
            result = create_demo_payroll_pipeline(
                company_id=options["company_id"],
                employee_external_id=options["employee_external_id"],
                payroll_id=options.get("payroll_id"),
                gross_pay=options["gross_pay"],
                allowances=options["allowances"],
                deductions=options["deductions"],
                net_pay=options["net_pay"],
                allow_existing=options["allow_existing"],
            )
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": options["company_id"],
                        "employee_external_id": options["employee_external_id"],
                        "payroll_id": options.get("payroll_id") or "zalary-payroll-demo-001",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("Demo payroll pipeline failed.") from exc

        self.stdout.write(json.dumps(result.safe_summary(), indent=2, sort_keys=True))
