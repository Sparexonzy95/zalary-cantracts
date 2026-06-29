# Zalary

Zalary is a Canton/Daml smart contract payroll system for token-instrument-aware salary workflows.

## USDCx Model

Zalary v0.4 uses the official USDCx instrument identity instead of text-only stablecoin names. A payroll token is represented as a `TokenInstrument` and compared by:

- `instrumentId`
- `instrumentAdmin`

The default TestNet/devnet USDCx configuration is:

- `symbol`: `USDCx`
- `instrumentId`: `USDCx`
- `instrumentAdmin`: `decentralized-usdc-interchain-rep::122049e2af8a725bd19759320fc83c638e7718973eac189d8f201309c512d1ffec61`
- `utilityApiUrl`: `https://api.utilities.digitalasset-staging.com`
- `xReserveApiUrl`: `https://xreserve-api-testnet.circle.com`

## Contract Flow

The intended company onboarding path is through `ZalaryConfig.CreateCompany`. This validates company token configuration against the active platform configuration before a company payroll workflow is created.

Zalary records payroll authorization, employee claims, settlement proof, payslips, settlement receipts, failed claim records, cancellation receipts, and leftover withdrawals.

## Transfer Integration

Zalary does not execute Canton Token Standard transfers inside Daml yet. The backend executes actual USDCx transfers through xReserve, Utilities, and the JSON Ledger API flow, then submits the resulting `TokenTransferProof` into Zalary.

Funding may include an optional transfer proof. Settlement requires a transfer proof whose token, sender, receiver, amount, and reference match the salary claim.

## Build And Test

```bash
daml build
daml test
```

The DAR is generated at:

```bash
.daml/dist/zalary-usdcx-contracts-0.1.0.dar
```
