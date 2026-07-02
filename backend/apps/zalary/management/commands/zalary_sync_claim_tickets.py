import json

from django.core.management.base import BaseCommand, CommandError

from apps.zalary.models import ClaimTicketMirror
from apps.zalary.services.errors import ZalaryBackendError, safe_error_message
from apps.zalary.services.sync import sync_claim_tickets


class Command(BaseCommand):
    help = "Sync active Zalary ClaimTicket contracts from the configured Ledger API party."

    def add_arguments(self, parser):
        parser.add_argument("--company-id")
        parser.add_argument("--payroll-id")
        parser.add_argument("--employee-external-id")

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        payroll_id = options.get("payroll_id")
        employee_external_id = options.get("employee_external_id")
        try:
            result = sync_claim_tickets(
                company_id=company_id,
                payroll_id=payroll_id,
                employee_external_id=employee_external_id,
            )
        except ZalaryBackendError as exc:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "error",
                        "company_id": company_id or "",
                        "payroll_id": payroll_id or "",
                        "employee_external_id": employee_external_id or "",
                        "error": safe_error_message(exc),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise CommandError("ClaimTicket sync failed.") from exc

        tickets = ClaimTicketMirror.objects.filter(contract_id__in=result.contract_ids).order_by(
            "company_id",
            "payroll_id",
            "employee_external_id",
        )
        summary = {
            "status": "ok",
            "synced_count": result.synced_count,
            "company_ids": result.company_ids,
            "payroll_ids": result.payroll_ids,
            "employee_external_ids": result.employee_external_ids,
            "contract_ids": result.contract_ids,
            "claim_tickets": [
                {
                    "contract_id": ticket.contract_id,
                    "company_id": ticket.company_id,
                    "payroll_id": ticket.payroll_id,
                    "employee_external_id": ticket.employee_external_id,
                    "ticket_amount": str(ticket.ticket_amount),
                    "source_allocation_contract_id": ticket.source_allocation_contract_id,
                }
                for ticket in tickets
            ],
        }
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
