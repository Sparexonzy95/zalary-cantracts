# Zalary

**Confidential payroll settlement infrastructure on Canton.**

Zalary is a Canton/Daml smart contract system for token-instrument-aware payroll workflows. It models payroll as a structured, permissioned, multi-party process between platform administrators, company administrators, HR wallets, employer funding wallets, and employee wallets.

The current implementation focuses on the on-ledger contract layer: platform token policy, company onboarding, employee enrollment, payroll vault creation, salary allocation, claim-ticket issuance, employee wage claims, settlement-proof validation, payslip generation, and audit records.

---

## Status

| Item | Status |
| --- | --- |
| Daml package | `zalary-usdcx-contracts` |
| Version | `0.1.0` |
| SDK | Daml SDK `3.5.1` |
| Target | Daml LF `2.3` |
| Build artifact | `.daml/dist/zalary-usdcx-contracts-0.1.0.dar` |
| Stored artifact | `artifacts/zalary-usdcx-contracts-0.1.0.dar` |
| Test script | `Zalary.Tests:payrollHappyPath` |
| Primary network target | Canton / 5N Sandbox DevNet workflow |

---

## Why Zalary Exists

Payroll settlement is usually fragmented across HR tools, spreadsheets, bank portals, approval chains, and reconciliation systems. This creates operational and compliance problems:

- Salary allocations are handled outside the settlement system.
- Employees have limited visibility into claim status.
- Funding and settlement proofs are disconnected from payroll records.
- Audit trails are scattered across multiple systems.
- Sensitive salary data can be exposed to parties that do not need it.
- Reconciliation becomes difficult when payroll intent and payment execution live in separate systems.

Zalary addresses this by turning payroll into a Canton workflow where each stage is represented by explicit contracts, choices, parties, permissions, and audit records.

---

## What Zalary Does

Zalary provides an on-ledger workflow for:

1. Platform-level token policy configuration
2. Company onboarding
3. HR and employer wallet authorization
4. Employee enrollment
5. Payroll vault creation
6. Salary allocation upload
7. Funding confirmation
8. Payroll activation
9. Claim-ticket issuance
10. Employee wage claim request
11. Employer settlement confirmation
12. Settlement proof validation
13. Payslip and settlement receipt creation
14. Failed claim recording
15. Payroll closure, cancellation, and leftover withdrawal

---

## Why Canton

Payroll is a naturally multi-party workflow. Different participants need different visibility and different authorization rights.

Zalary uses Canton/Daml because Canton is designed for:

- **Explicit authorization**: Daml templates and choices define who must sign and who can act.
- **Selective visibility**: Contracts disclose data only to entitled stakeholders.
- **Private workflows**: Salary and settlement data do not need to be globally visible.
- **Auditability**: Important payroll actions create durable ledger records.
- **Enterprise workflows**: Daml is well suited for regulated, multi-party financial processes.

Zalary uses these properties to model payroll without relying on a public-everything blockchain design.

---

## Architecture Overview

Zalary is organized around six main Daml modules plus the script test module.

```text
daml/Zalary
|-- Audit.daml
|-- Company.daml
|-- Enrollment.daml
|-- Payroll.daml
|-- Platform.daml
|-- Tests.daml
`-- Types.daml
```

### `Zalary.Types`

Defines shared domain types and validation helpers.

Key types include:

- `PayrollStatus`
- `AllocationStatus`
- `ClaimStatus`
- `ClaimAction`
- `PayrollPeriod`
- `TokenInstrument`
- `TokenTransferProof`
- `SalaryBreakdown`

This module also includes validation helpers such as:

- `validTokenInstrument`
- `sameTokenInstrument`
- `tokenInList`
- `tokensUnique`
- `validTokenTransferProof`
- `validPayrollPeriod`
- `validSalaryBreakdown`

### `Zalary.Platform`

Defines platform-level configuration through `ZalaryConfig`.

The platform config controls:

- the platform admin
- globally supported settlement tokens
- the default token
- whether the platform configuration is active

The intended onboarding path for companies is through:

```daml
ZalaryConfig.CreateCompany
```

This ensures that a company can only be created with token instruments that are supported by the active platform configuration.

`ZalaryConfig.UpdateCompanyAllowedTokens` applies the same platform-level token validation when changing a company's allowed settlement tokens.

### `Zalary.Company`

Defines the company registry and role authority.

The `Company` contract stores:

- platform admin
- company admin
- company name and ID
- admin wallets
- HR wallets
- employer/funding wallets
- allowed settlement tokens

The company contract supports choices for:

- adding and removing HR wallets
- adding and removing employer wallets
- creating employee enrollments
- creating payroll vaults

### `Zalary.Enrollment`

Defines employee enrollment.

The `EmployeeEnrollment` contract links:

- HR wallet
- employer wallet
- employee wallet
- employee external ID
- company ID
- enrollment status

Employees are observers on their enrollment and later prove control by requesting salary claims through claim tickets.

### `Zalary.Payroll`

Defines the core payroll lifecycle.

Important templates include:

- `PayrollVault`
- `SalaryAllocation`
- `ClaimTicket`
- `SalaryClaim`
- `ClaimActionAuthorization`

The payroll module controls the main state transitions:

```text
Created
  -> AllocationsFinalized
  -> Funded
  -> Active
  -> Closed
```

It also supports cancellation paths:

```text
Created / AllocationsFinalized -> CancelledByHR
Funded -> CancelledAfterFunding
```

### `Zalary.Audit`

Defines audit and receipt records created during payroll execution.

Important templates include:

- `FundingReceipt`
- `PayrollCancellationReceipt`
- `LeftoverWithdrawalReceipt`
- `SettlementReceipt`
- `Payslip`
- `SettledSalaryRecord`
- `FailedSalaryClaim`

These contracts make payroll actions traceable and auditable for the entitled parties.

---

## Token Instrument Model

Zalary uses a token instrument model rather than treating settlement assets as plain text labels.

A payroll token is represented by:

```daml
data TokenInstrument = TokenInstrument
  with
    symbol : Text
    instrumentId : Text
    instrumentAdmin : Party
    utilityApiUrl : Text
    xReserveApiUrl : Text
  deriving (Eq, Show)
```

Token identity is compared by:

```text
instrumentId
instrumentAdmin
```

This allows Zalary to distinguish actual token instruments instead of relying only on symbols such as `USDCx`.

---

## USDCx Configuration

The default TestNet/DevNet USDCx configuration used by the tests is:

| Field | Value |
| --- | --- |
| `symbol` | `USDCx` |
| `instrumentId` | `USDCx` |
| `instrumentAdmin` | `decentralized-usdc-interchain-rep::122049e2af8a725bd19759320fc83c638e7718973eac189d8f201309c512d1ffec61` |
| `utilityApiUrl` | `https://api.utilities.digitalasset-staging.com` |
| `xReserveApiUrl` | `https://xreserve-api-testnet.circle.com` |

This makes USDCx a configured settlement token for the current checkpoint while keeping the design extensible to other Canton-compatible settlement tokens.

---

## Transfer Proof Model

Zalary does not currently execute Canton Token Standard or USDCx transfers directly inside Daml.

Instead, the expected integration model is:

1. The backend initiates or observes the actual token transfer through the relevant token infrastructure.
2. The backend receives or constructs a transfer proof.
3. The backend submits the proof into the Zalary workflow as a `TokenTransferProof`.
4. Zalary validates the proof fields before recording funding or settlement.

A transfer proof contains:

```daml
data TokenTransferProof = TokenTransferProof
  with
    token : TokenInstrument
    sender : Party
    receiver : Party
    amount : Decimal
    transferReference : Text
    transferInstructionCid : Optional Text
    holdingCid : Optional Text
    executedAt : Time
  deriving (Eq, Show)
```

Funding may include an optional transfer proof.

Settlement requires a transfer proof whose:

- token matches the payroll token
- sender matches the employer wallet
- receiver matches the employee wallet
- amount matches the salary claim amount
- reference matches the settlement reference

This design keeps Zalary focused on payroll workflow integrity while allowing payment execution to be handled by the backend and token integration layer.

---

## Payroll Lifecycle

The intended happy path is:

```text
1. Platform admin creates ZalaryConfig
2. Platform admin and company admin create Company through ZalaryConfig.CreateCompany
3. HR creates EmployeeEnrollment
4. HR creates PayrollVault
5. HR adds SalaryAllocation records
6. HR finalizes allocations
7. Employer confirms funding
8. Employer activates payroll
9. HR issues claim tickets
10. Employee requests salary claim
11. Employer confirms settlement with transfer proof
12. Zalary records settlement receipt, payslip, and settled salary record
```

---

## Roles

### Platform Admin

The platform admin governs the platform-level configuration and supported settlement tokens.

### Company Admin

The company admin governs company-side authority and is included in company-level contracts and audit visibility.

### HR Wallet

The HR wallet prepares the payroll workflow:

- creates employee enrollments
- creates payroll vaults
- uploads salary allocations
- finalizes allocations
- issues claim tickets
- may close payroll after the claim window

### Employer Wallet

The employer wallet controls funding and settlement actions:

- confirms funding
- activates payroll
- confirms settlement
- rejects claims where applicable
- closes payroll
- withdraws leftovers after closure or eligible cancellation

### Employee Wallet

The employee wallet receives claim tickets, requests salary claims, and observes relevant payroll records such as payslips and settlement outcomes.

---

## Privacy and Visibility

Zalary uses Daml signatories and observers to control visibility.

Examples:

- Company contracts are signed by the platform admin and company admin, while relevant admin, HR, and employer wallets are observers.
- Employee enrollments are signed by HR and company admin, while the employee and employer are observers.
- Payroll vaults are signed by HR and company admin, while the employer wallet observes.
- Salary allocations are visible to HR, company admin, employer wallet, and the relevant employee wallet.
- Settlement receipts and payslips are visible to the employer, HR, employee, and company admin.

This allows Zalary to avoid making all payroll data public while still preserving traceability for the entitled workflow participants.

---

## Audit Records

Zalary creates explicit contracts for important payroll events.

| Record | Purpose |
| --- | --- |
| `FundingReceipt` | Records employer funding confirmation |
| `SettlementReceipt` | Records successful salary settlement |
| `Payslip` | Records employee payslip details |
| `SettledSalaryRecord` | Records settled salary state |
| `FailedSalaryClaim` | Records rejected or failed claims |
| `PayrollCancellationReceipt` | Records payroll cancellation |
| `LeftoverWithdrawalReceipt` | Records withdrawal of unused funded balance |

These contracts create a structured audit trail around payroll funding, claims, settlement, and closure.

---

## Project Structure

```text
.
|-- artifacts
|   `-- zalary-usdcx-contracts-0.1.0.dar
|-- daml
|   `-- Zalary
|       |-- Audit.daml
|       |-- Company.daml
|       |-- Enrollment.daml
|       |-- Payroll.daml
|       |-- Platform.daml
|       |-- Tests.daml
|       `-- Types.daml
|-- daml.yaml
`-- README.md
```

---

## Requirements

Install the Daml SDK toolchain compatible with the project.

This repository is configured with:

```yaml
sdk-version: 3.5.1
name: zalary-usdcx-contracts
version: 0.1.0
source: daml
init-script: Zalary.Tests:payrollHappyPath
build-options:
  - --target=2.3
```

---

## Build

Run the classic Daml build path:

```bash
daml build
```

Expected output:

```text
.daml/dist/zalary-usdcx-contracts-0.1.0.dar
```

Some local and Seaport-adjacent environments also expose DPM-compatible commands. In those environments, the equivalent build check is:

```bash
dpm build
```

---

## Test

Run:

```bash
daml test
```

The test suite includes the `payrollHappyPath` script and additional validation scenarios around platform configuration, claim windows, settlement proofs, and payroll workflow behavior.

If your environment exposes DPM-compatible commands, the equivalent test check is:

```bash
dpm test
```

---

## DAR Artifact

After building, the deployable DAR is available at:

```text
.daml/dist/zalary-usdcx-contracts-0.1.0.dar
```

A copy is also stored in:

```text
artifacts/zalary-usdcx-contracts-0.1.0.dar
```

---

## Deployment Notes

Zalary is intended to be deployed as a Daml package to a Canton validator.

For hackathon usage, the project can be deployed through Seaport to the shared 5N Sandbox DevNet validator.

Typical deployment flow:

1. Build the DAR.
2. Upload or deploy the DAR to the validator.
3. Create a `ZalaryConfig` contract.
4. Create a company through `ZalaryConfig.CreateCompany`.
5. Execute the payroll workflow from enrollment to settlement.

---

## Current Limitations

Zalary is currently the on-ledger workflow layer. It does not yet include:

- a production frontend
- backend automation for every payroll step
- direct in-Daml execution of token transfers
- production-grade off-ledger integration with xReserve, Utilities, or wallet APIs
- enterprise identity management
- accounting system integrations
- production payroll compliance adapters

These are intended future integration layers around the core Daml workflow.

---

## Roadmap

Planned next steps:

- Build backend automation for end-to-end payroll runs
- Integrate transfer-proof submission with employer settlement confirmation
- Add role-based frontend views for employer, HR, and employee users
- Display employee payslips and claim status in the UI
- Connect payroll workflow events to off-ledger services
- Improve reporting and audit export
- Prepare a full end-to-end demo from payroll vault creation to settlement confirmation

---

## Security Notes

This repository is for hackathon and development use.

Before production use, Zalary would require:

- full security review of all Daml templates and choices
- review of role assumptions and visibility boundaries
- backend authentication and authorization hardening
- secure handling of transfer proofs
- replay protection for off-ledger payment references
- production-grade monitoring and audit export
- legal and compliance review for payroll usage

---

## Summary

Zalary demonstrates how payroll settlement can be modeled as a confidential, permissioned Canton workflow.

The core contribution is a structured Daml contract system that connects company onboarding, HR authorization, employee enrollment, payroll allocation, funding confirmation, wage claims, transfer-proof validation, payslip generation, and audit records into one coherent on-ledger process.
