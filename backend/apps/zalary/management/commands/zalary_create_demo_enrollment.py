import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import CompanyMirror
from apps.zalary.services.auth import single_party_demo_enabled
from apps.zalary.services.enrollment import create_employee_enrollment
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message


class Command(BaseCommand):
    help = "Create a demo EmployeeEnrollment through Company.CreateEmployeeEnrollment."

    def add_arguments(self, parser):
        parser.add_argument("--company-id", required=True)
        parser.add_argument("--employee-external-id", required=True)
        parser.add_argument("--hr-wallet")
        parser.add_argument("--employer-wallet")
        parser.add_argument("--employee-wallet")
        parser.add_argument(
            "--allow-existing",
            action="store_true",
            help="Return the existing mirrored enrollment instead of submitting a duplicate create command.",
        )

    def handle(self, *args, **options):
        company_id = options["company_id"]
        employee_external_id = options["employee_external_id"]
        try:
            company = CompanyMirror.objects.filter(company_id=company_id).order_by("-synced_at").first()
            if company is None:
                raise CompanyMirror.DoesNotExist()

            hr_wallet = options.get("hr_wallet") or _first_wallet(company.hr_wallet_parties, "hrWallet")
            employer_wallet = options.get("employer_wallet") or _first_wallet(
                company.employer_wallet_parties,
                "employerWallet",
            )
            employee_wallet = options.get("employee_wallet")
            if not employee_wallet:
                if single_party_demo_enabled():
                    employee_wallet = hr_wallet
                elif not options["allow_existing"]:
                    raise ValueError("employeeWallet is required unless ZALARY_ALLOW_SINGLE_PARTY_DEMO=true.")
                else:
                    employee_wallet = ""

            result = create_employee_enrollment(
                company_id=company_id,
                hr_wallet=hr_wallet,
                employer_wallet=employer_wallet,
                employee_wallet=employee_wallet,
                employee_external_id=employee_external_id,
                sync_after=True,
                allow_existing=options["allow_existing"],
            )
        except (CompanyMirror.DoesNotExist, ZalaryBackendError, ValueError) as exc:
            error_message = (
                f"Company not found in local mirror: {company_id}."
                if isinstance(exc, CompanyMirror.DoesNotExist)
                else safe_error_message(exc)
            )
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": company_id,
                        "employee_external_id": employee_external_id,
                        "error": error_message,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("Demo enrollment creation failed.") from exc

        self.stdout.write(json.dumps(result.safe_summary(), indent=2, sort_keys=True))


def _first_wallet(values: list[str], field_name: str) -> str:
    for value in values or []:
        cleaned = (value or "").strip()
        if cleaned:
            return cleaned
    raise ValueError(f"{field_name} is required but no mirrored company wallet is available.")
