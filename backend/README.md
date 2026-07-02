# Zalary Backend

This folder contains the Zalary backend integration layer.

The repository is still primarily a Daml package, so the backend is self-contained under `backend/`. It includes a minimal local Django project, SQLite settings, Django REST Framework wiring, Ledger API health checks, active-contract sync for the deployed Zalary templates used by the demo flow, and targeted Ledger command submission for onboarding and payroll execution.

## Current Contract Package

| Item | Value |
| --- | --- |
| Daml package | `zalary-usdcx-contracts` |
| DAR | `zalary-usdcx-contracts-0.1.0.dar` |
| Module namespace | `Zalary.*` |
| Live config template | `Zalary.Platform:ZalaryConfig` |

## Implemented Health Check

The backend exposes:

```text
GET /api/zalary/ledger/health/
```

It calls:

```text
GET {ZALARY_LEDGER_API_URL}/v2/state/ledger-end
```

Expected successful response shape:

```json
{
  "status": "ok",
  "ledger_api_url_configured": true,
  "auth_configured": true,
  "ledger_end": {}
}
```

The endpoint never returns bearer tokens, client secrets, or raw authentication payloads.

You can run the same check through:

```bash
python backend/manage.py zalary_ledger_health
```

## Implemented ZalaryConfig Sync

The backend exposes a read-only local mirror for active `Zalary.Platform:ZalaryConfig` contracts:

```text
GET /api/zalary/config/
POST /api/zalary/config/sync/
```

The sync path calls the Ledger API active-contract read endpoint:

```text
POST {ZALARY_LEDGER_API_URL}/v2/state/active-contracts
```

It uses the configured read party from `ZALARY_LEDGER_PARTY`, falling back to `ZALARY_DEFAULT_READ_AS`, `ZALARY_DEFAULT_ACT_AS`, and then the devnet party listed in `.env.example`.

The query first requests `Zalary.Platform:ZalaryConfig` by template filter using the package-name reference `#zalary-usdcx-contracts`. If the template-filtered response is empty, the backend performs one wildcard active-contract read for the same party and filters locally to `ZalaryConfig` only. No other template mirrors are written by this task.

When a previously synced config contract is missing from a later active-contract sync, the mirror row is marked `ledger_active=false`. The Daml payload field `isActive` is kept separately as `is_active`.

You can run the sync from the command line:

```bash
python backend/manage.py zalary_sync_config
```

If sync returns zero rows, run the safe active-contract diagnostic command:

```bash
python backend/manage.py zalary_debug_active_contracts
```

The diagnostic command prints configuration presence, selected read party, ledger end, wildcard active-contract counts, unique template IDs, truncated contract IDs, and whether the configured `ZALARY_PLATFORM_CONFIG_CONTRACT_ID` appears in the active-contract result. It does not print bearer tokens, client secrets, authorization headers, raw auth responses, or full contract payloads.

## Payroll Execution Commands

The remaining payroll execution flow is implemented through `ClaimTicket.RequestSalaryClaim` and `SalaryClaim.ConfirmSalarySettlement`.

Production settlement fails closed by default. The backend will not invent `TokenTransferProof` data unless `ZALARY_ENABLE_DEMO_SETTLEMENT_PROOF=true`, which is local/dev only. A real settlement must use either a configured token-transfer provider or an explicitly allowed externally supplied proof.

Read-only final-state sync commands:

```bash
python backend/manage.py zalary_sync_salary_claims
python backend/manage.py zalary_sync_settlement_receipts
python backend/manage.py zalary_sync_payslips
python backend/manage.py zalary_sync_settled_salary_records
python backend/manage.py zalary_sync_failed_salary_claims
```

Demo claim request:

```bash
python backend/manage.py zalary_request_demo_salary_claim --company-id <company-id> --payroll-id <payroll-id> --employee-external-id EMP-001
```

Production full execution:

```bash
python backend/manage.py zalary_run_full_payroll_execution --company-id <company-id> --payroll-id <payroll-id> --employee-external-id EMP-001 --settlement-reference <reference>
```

If a trusted external proof verifier is used, pass the proof only when `ZALARY_USDCX_ALLOW_EXTERNAL_PROOF=true`:

```bash
python backend/manage.py zalary_run_full_payroll_execution --company-id <company-id> --payroll-id <payroll-id> --settlement-proof-json '{"token":{},"sender":"","receiver":"","amount":"0.0000000000","transferReference":"","executedAt":""}'
```

Local/dev demo execution:

```bash
python backend/manage.py zalary_create_demo_full_payroll_execution --company-id <company-id> --payroll-id <payroll-id> --employee-external-id EMP-001 --settlement-reference <reference> --demo-proof
```

`--demo-proof` requires `ZALARY_ENABLE_DEMO_SETTLEMENT_PROOF=true` and should only be used for local/dev validation.

HTTP endpoints:

```text
POST /api/zalary/salary-claims/request/
POST /api/zalary/salary-claims/{id}/confirm-settlement/
POST /api/zalary/companies/{company_id}/full-payroll-execution/create-demo/
```

Settlement confirmation validates that the proof token, sender, receiver, amount, and transfer reference match the mirrored `SalaryClaim` before command submission.

Frontend action responses for salary claim request and settlement confirmation use this envelope:

```json
{
  "status": "ok",
  "action": "ConfirmSalarySettlement",
  "resource": {},
  "ledger": {
    "command_id": "",
    "update_id": "",
    "ledger_command_id": 0
  },
  "transfer": {
    "provider": "",
    "status": "",
    "transfer_instruction_cid": "",
    "holding_cid": ""
  },
  "sync": {},
  "next_actions": ["view_payslip"]
}
```

Errors use:

```json
{
  "status": "error",
  "code": "settlement_invalid",
  "message": "Safe message only.",
  "details": {}
}
```

## Token Transfer Provider

The provider boundary lives under `apps.zalary.services.token_transfers`.

`TokenTransferProvider.execute_transfer` receives a `TokenTransferRequest` and must return `completed`, `pending`, `failed`, or `unavailable`. Only `completed` results may produce a Daml-shaped `TokenTransferProof`. Pending, failed, and unavailable results never submit `SalaryClaim.ConfirmSalarySettlement`.

The USDCx provider now implements the Canton Token Standard integration boundary:

- queries active holdings through the configured `HoldingV1` interface
- filters holdings by owner party, instrument id/admin, positive amount, and lock status
- selects a deterministic smallest sufficient input-holding set
- discovers TransferFactory details through the Utility API registry endpoint:
  `POST /registry/transfer-instruction/v1/transfer-factory`
- builds a dry-run `TransferFactory_Transfer` command plan
- submits a live transfer only when the registry returns `factoryId`, `transferKind`, and `choiceContext.choiceContextData`
- parses completed, pending, and failed transfer results
- builds `TokenTransferProof` only from completed transfers

The provider remains fail-closed when the TransferFactory registry is unavailable or returns an unknown `transferKind`. It does not invent `factoryId`, `choiceContextData`, disclosed contracts, holding CIDs, transfer instruction CIDs, or successful transfer proofs. The default argument shape is `transfer_extra_args`; `canonical_flat` is available as a retry shape when explicitly enabled.

Safe diagnostics:

```bash
python backend/manage.py zalary_discover_usdcx_transfer_schema --party <employer-party> --instrument-id USDCx --instrument-admin <instrument-admin> --json
python backend/manage.py zalary_usdcx_transfer_dry_run --sender-party <employer-party> --receiver-party <employee-party> --amount 1100.0000000000 --instrument-id USDCx --instrument-admin <instrument-admin> --settlement-reference <reference> --json
```

Live token transfer only, without confirming Zalary settlement:

```bash
python backend/manage.py zalary_usdcx_transfer --sender-party <employer-party> --receiver-party <employee-party> --amount 1100.0000000000 --instrument-id USDCx --instrument-admin <instrument-admin> --settlement-reference <reference> --allow-pending --json
```

If a Token Standard transfer returns `pending`, the backend records the transfer attempt and returns a pending settlement result. It does not call `SalaryClaim.ConfirmSalarySettlement` until a completed transfer proof is available.

### Sandbox ZUSD Test Token

ZUSD is a Daml-only sandbox token for end-to-end frontend and settlement testing when live USDCx transfer infrastructure is unavailable. It is not a replacement for USDCx in production.

The sandbox contracts live under `Zalary.Sandbox.ZUSD`:

- `ZUSDIssuer` mints ZUSD through `MintZUSD`
- `ZUSDHolding` tracks owner balances and transfers through `TransferZUSD`
- `ZUSDFaucetGrant` records faucet receipts
- `ZUSDFaucetConfig` can model faucet limits on-ledger later

Backend endpoints:

```text
GET /api/zalary/sandbox/zusd/balance/?owner_party=<party>
POST /api/zalary/sandbox/zusd/faucet/request
GET /api/zalary/sandbox/zusd/faucet/history/?owner_party=<party>
```

Management commands:

```bash
python backend/manage.py zalary_zusd_mint --owner-party <EMPLOYER_PARTY> --amount 5000.0000000000 --reference faucet-request-manual-001 --json
python backend/manage.py zalary_zusd_balance --owner-party <EMPLOYER_PARTY> --json
python backend/manage.py zalary_zusd_transfer_dry_run --sender-party <EMPLOYER_PARTY> --receiver-party <EMPLOYEE_PARTY> --amount 1.0000000000 --settlement-reference zalary-zusd-dry-run-001 --json
python backend/manage.py zalary_zusd_transfer --sender-party <EMPLOYER_PARTY> --receiver-party <EMPLOYEE_PARTY> --amount 1.0000000000 --settlement-reference zalary-zusd-live-001 --json
```

To use ZUSD as the settlement provider for the existing payroll path, set:

```text
ZALARY_TOKEN_TRANSFER_PROVIDER=zalary_test_token
ZALARY_TEST_TOKEN_ENABLED=true
ZALARY_TEST_TOKEN_ENVIRONMENT=sandbox
ZALARY_TEST_TOKEN_ISSUER_PARTY=<issuer-party>
ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID=<optional-zusdissuer-cid>
```

The ZUSD provider is fail-closed. It only returns a completed `TokenTransferProof` after a real ledger `TransferZUSD` command produces a receiver holding with the exact receiver party, amount, symbol, and settlement reference. Failed, unavailable, pending, or ambiguous results never call `SalaryClaim.ConfirmSalarySettlement`.

Funding flow v1 is balance-check only. There is no ZUSD escrow vault yet; the backend can confirm payroll settlement only after an employer-to-employee ZUSD transfer has completed and the proof validates against the `SalaryClaim`.
## Planned Backend Responsibilities

- Authenticate requests to the Canton/Daml Ledger API.
- Track Party IDs and role mappings for platform, company, HR, employer, and employee users.
- Query active contracts by template ID and party visibility.
- Sync `ZalaryConfig`, company, enrollment, payroll, allocation, claim, and receipt contracts into local mirror tables.
- Submit Ledger API commands with `actAs`, `readAs`, `commandId`, template identifiers, choice names, and payloads.
- Track submitted commands through status, `updateId`, ledger offset, and error details.
- Build and submit payroll workflow commands without changing the Daml contract layer.
- Accept settlement proof data from the future token integration layer and pass it into Zalary command payloads.

## Environment Variables

Use deployment configuration or an untracked `.env` file for real values. The committed `.env.example` lists the expected names.

| Variable | Purpose |
| --- | --- |
| `ZALARY_LEDGER_API_URL` | Base URL for the Ledger API gateway used by the backend. |
| `ZALARY_LEDGER_API_AUTH_TOKEN` | Optional pre-issued bearer token. |
| `ZALARY_LEDGER_API_TOKEN_URL` | Optional OAuth/OIDC token endpoint. |
| `ZALARY_LEDGER_API_CLIENT_ID` | Optional OAuth/OIDC client ID. |
| `ZALARY_LEDGER_API_CLIENT_SECRET` | Optional OAuth/OIDC client secret. |
| `ZALARY_LEDGER_API_AUDIENCE` | Optional token audience. |
| `ZALARY_LEDGER_API_TOKEN_SCOPE` | Optional token scope, default `daml_ledger_api`. |
| `ZALARY_LEDGER_API_TLS_CA_FILE` | Optional CA bundle path when the Ledger API requires custom TLS trust. |
| `ZALARY_LEDGER_API_TIMEOUT_SECONDS` | Request timeout for future Ledger API calls. |
| `ZALARY_DAML_PACKAGE_NAME` | Expected package name, currently `zalary-usdcx-contracts`. |
| `ZALARY_DAML_PACKAGE_ID` | Deployed package ID/hash when required by the API client. |
| `ZALARY_LEDGER_PARTY` | Party ID used for read-only active-contract queries. |
| `ZALARY_PLATFORM_ADMIN_PARTY` | Platform admin Party ID used for platform-scoped operations. |
| `ZALARY_PLATFORM_CONFIG_CONTRACT_ID` | Live `ZalaryConfig` ContractId once known. |
| `ZALARY_DEFAULT_ACT_AS` | Comma-separated Party IDs for local development defaults. |
| `ZALARY_DEFAULT_READ_AS` | Comma-separated Party IDs for local development defaults. |
| `ZALARY_COMMAND_ID_PREFIX` | Prefix for generated command IDs. |
| `ZALARY_SYNC_START_OFFSET` | Optional starting ledger offset for sync jobs. |
| `ZALARY_SYNC_PAGE_SIZE` | Page size for future active-contract and update sync jobs. |
| `ZALARY_ENABLE_DEMO_SETTLEMENT_PROOF` | Local/dev only. Enables backend-generated demo settlement proof. Default false. |
| `ZALARY_ALLOW_UNBOUND_DEMO_AUTH` | Local/dev only. Allows operational endpoints without an authenticated party mapping. Default false. |
| `ZALARY_TOKEN_TRANSFER_PROVIDER` | Production token transfer provider selector. Empty means unavailable/fail closed. |
| `ZALARY_USDCX_TRANSFER_PROVIDER` | USDCx provider mode/config selector. Empty means unavailable/fail closed. |
| `ZALARY_USDCX_UTILITY_API_URL` | Utility API base URL placeholder for future USDCx integration. |
| `ZALARY_USDCX_XRESERVE_API_URL` | xReserve API base URL placeholder for future USDCx integration. |
| `ZALARY_USDCX_ALLOW_EXTERNAL_PROOF` | Allows externally supplied settlement proof after strict claim validation. Default false. |
| `ZALARY_USDCX_TRANSFER_TIMEOUT_SECONDS` | Timeout for USDCx provider calls. |
| `ZALARY_USDCX_HOLDING_INTERFACE_ID` | Canton Token Standard Holding interface id. |
| `ZALARY_USDCX_TRANSFER_FACTORY_INTERFACE_ID` | Canton Token Standard TransferFactory interface id. |
| `ZALARY_USDCX_TRANSFER_INSTRUCTION_INTERFACE_ID` | Canton Token Standard TransferInstruction interface id. |
| `ZALARY_USDCX_TRANSFER_FACTORY_ENDPOINT` | Utility API registry endpoint. DevNet/TestNet staging uses `https://api.utilities.digitalasset-staging.com/registry/transfer-instruction/v1/transfer-factory`. |
| `ZALARY_USDCX_ALLOW_CANONICAL_TRANSFER_ARGUMENT` | Allows retrying the legacy `canonical_flat` argument shape if `transfer_extra_args` is rejected. |
| `ZALARY_USDCX_TRANSFER_ARGUMENT_SHAPE` | TransferFactory argument shape. Default/current target: `transfer_extra_args`. |
| `ZALARY_USDCX_AUTO_ACCEPT_PENDING_TRANSFER` | Default false. Pending transfer auto-accept remains disabled unless receiver signing is explicitly implemented. |
| `ZALARY_TEST_TOKEN_ENABLED` | Enables sandbox ZUSD faucet minting. Default false. |
| `ZALARY_TEST_TOKEN_ENVIRONMENT` | Must be `sandbox` for ZUSD faucet and reads. |
| `ZALARY_TEST_TOKEN_ISSUER_PARTY` | Party controlling `ZUSDIssuer.MintZUSD`. |
| `ZALARY_TEST_TOKEN_ISSUER_CONTRACT_ID` | Optional active `ZUSDIssuer` ContractId. If empty, backend queries visible issuer contracts. |
| `ZALARY_TEST_TOKEN_MAX_GRANT_AMOUNT` | Max amount per ZUSD faucet request. |
| `ZALARY_TEST_TOKEN_DAILY_LIMIT` | Max daily ZUSD faucet amount per owner party. |
| `ZALARY_TEST_TOKEN_MONTHLY_LIMIT` | Max monthly ZUSD faucet amount per owner party. |
| `DJANGO_SECRET_KEY` | Local Django secret key. Use a real secret outside local development. |
| `DJANGO_DEBUG` | Local debug flag. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts. |

## Package Layout

```text
backend
|-- .env.example
|-- README.md
|-- manage.py
|-- requirements.txt
|-- apps
|   `-- zalary
|       |-- api.py
|       |-- apps.py
|       |-- management
|       |-- models.py
|       |-- permissions.py
|       |-- selectors.py
|       |-- serializers.py
|       |-- urls.py
|       `-- services
|           |-- auth.py
|           |-- commands.py
|           |-- errors.py
|           |-- ledger.py
|           |-- payloads.py
|           |-- settlement.py
|           |-- sync.py
|           |-- tasks.py
|           |-- token_transfers
|           `-- templates.py
`-- zalary_backend
    |-- asgi.py
    |-- settings.py
    |-- urls.py
    `-- wsgi.py
```

## Running Locally

Install dependencies:

```bash
python -m pip install -r backend/requirements.txt
```

Create a local untracked environment file from `backend/.env.example`, then fill the Ledger API settings supplied by the 5N Sandbox environment.

Run local checks and migrations:

```bash
python backend/manage.py check
python backend/manage.py makemigrations zalary
python backend/manage.py migrate
```

Start the local Django server:

```bash
python backend/manage.py runserver
```

Check Ledger API health through HTTP:

```bash
curl http://127.0.0.1:8000/api/zalary/ledger/health/
```

## Service Boundaries

`services.auth`
: Reads configuration, supports a pre-issued bearer token, or obtains an OAuth/OIDC client-credentials token with in-memory expiry-aware caching.

`services.templates`
: Defines deployed Zalary template and choice constants.

`services.payloads`
: Builds Daml-shaped payload dictionaries for future command submission.

`services.ledger`
: Owns Ledger API operations. `get_current_ledger_offset` and read-only active-contract query for `ZalaryConfig` are implemented today.

`services.commands`
: Provides workflow-level wrappers for company creation, payroll vault creation, allocation upload, claim ticket issuance, salary claim request, and settlement confirmation.

`services.sync`
: Owns mirroring from active contracts into local Django models, including `ZalaryConfig`, company, enrollment, payroll vault, salary allocation, claim ticket, salary claim, and final audit contracts.

`services.settlement`
: Owns salary claim request, settlement proof validation, salary settlement confirmation, transfer-record persistence, and full payroll execution helpers.

`services.token_transfers`
: Owns the provider boundary for real token settlement. The USDCx provider can read Token Standard holdings and build/submit transfer commands only when the configured factory discovery endpoint supplies the exact live choice argument.

`services.request_context`
: Provides authenticated party binding helpers. Operational endpoints must not trust body-supplied party IDs as authority.

`services.idempotency`
: Provides deterministic business workflow IDs and pending/succeeded command checks before risky ledger submissions.

`services.tasks`
: Holds async job entry points for future Celery, Dramatiq, RQ, or management command integration.

## Remaining Frontend Blocker

The backend is now safe to integrate with the frontend. Real end-to-end settlement still depends on the live Utility API registry accepting the chosen `TransferFactory_Transfer` argument shape and the Ledger API accepting the final choice. Production settlement will not call `SalaryClaim.ConfirmSalarySettlement` unless the token transfer completed and produced a real proof.
