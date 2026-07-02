def sync_platform_config_task() -> None:
    # TODO: Wire to the selected async runner and call services.sync.sync_zalary_config.
    raise NotImplementedError("Platform config sync task is not implemented yet.")


def sync_active_contracts_task() -> None:
    # TODO: Wire to the selected async runner and call services.sync.sync_active_contracts.
    raise NotImplementedError("Active contract sync task is not implemented yet.")


def poll_ledger_command_task(command_id: str) -> None:
    # TODO: Poll the Ledger API for command completion and update LedgerCommand status.
    raise NotImplementedError(f"Command polling for {command_id} is not implemented yet.")


def sync_payroll_state_task(company_id: str, payroll_id: str) -> None:
    # TODO: Sync payroll vault, allocations, claims, and audit records for one payroll run.
    raise NotImplementedError(f"Payroll sync for {company_id}/{payroll_id} is not implemented yet.")
