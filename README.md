@'
# Zalary

Zalary is a Canton/Daml smart contract payroll system for stablecoin salary workflows.

This checkpoint submission focuses on the contract layer.

## What is included

- Platform stablecoin configuration
- Company onboarding
- HR and employer wallet permissions
- Employee enrollment
- Payroll vault creation
- Salary allocation
- Funding confirmation
- Payroll activation
- Claim ticket issuance
- Employee salary claim
- Employer settlement confirmation
- Payslip creation
- Settlement receipts
- Failed claim records
- Payroll cancellation
- Leftover withdrawal

## Contract flow

The intended company onboarding path is through `ZalaryConfig.CreateCompany`.

This validates company token configuration against the active platform configuration before a company payroll workflow is created.

## Build and test

Build:

```bash
dpm build