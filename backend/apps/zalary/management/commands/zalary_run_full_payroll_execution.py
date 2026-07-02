import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.settlement import run_full_payroll_execution


class Command(BaseCommand):
    help = "Run production payroll execution using a real token provider or explicitly allowed external proof."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", required=True)
        parser.add_argument("--payroll-id", required=True)
        parser.add_argument("--employee-external-id", default="EMP-001")
        parser.add_argument("--funding-amount")
        parser.add_argument("--funding-reference")
        parser.add_argument("--settlement-reference")
        parser.add_argument(
            "--settlement-proof-json",
            help="Optional external TokenTransferProof JSON. Requires ZALARY_USDCX_ALLOW_EXTERNAL_PROOF=true.",
        )
        parser.add_argument(
            "--allow-existing",
            action="store_true",
            help="Reuse already completed claim and settlement steps.",
        )

    def handle(self, *args, **options):
        settlement_proof = None
        if options.get("settlement_proof_json"):
            try:
                settlement_proof = json.loads(options["settlement_proof_json"])
            except ValueError as exc:
                raise CommandError("--settlement-proof-json must be valid JSON.") from exc

        try:
            result = run_full_payroll_execution(
                company_id=options["company_id"],
                payroll_id=options["payroll_id"],
                employee_external_id=options["employee_external_id"],
                funding_amount=options.get("funding_amount"),
                funding_reference=options.get("funding_reference"),
                settlement_reference=options.get("settlement_reference"),
                settlement_proof=settlement_proof,
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
            raise CommandError("Production full payroll execution failed.") from exc

        self.stdout.write(json.dumps(result.safe_summary(), indent=2, sort_keys=True))
