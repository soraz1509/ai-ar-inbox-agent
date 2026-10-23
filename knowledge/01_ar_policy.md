# AR Policy - BestRun DE (company code 1010)
<!-- Synthetic demo policy for the AR Inbox-to-Cash Agent portfolio project. Not a real company policy. -->

## Scope and principles
Applies to all customer receivables of company code 1010 (BestRun DE), currency EUR.
The AR agent prepares proposals and drafts only. Nothing is posted, cleared or written off
without approval by an AR collector. Every proposal must reference SAP document numbers and
the policy section it relies on.

## Payment differences and tolerances
A payment difference is the open amount minus the payment minus deductions the customer stated.
Tolerance is the lower of EUR 5.00 and 0.5 % of the payment amount.
- Difference within tolerance: propose write-off to payment differences; collector approves.
- Difference above tolerance: never write off. The residual stays open on the customer account.
  If the customer gave a reason, create a dispute case (see dispute reason code catalogue).
  If no reason was given, ask the customer in the reply and keep the residual open.
- Over-payment: keep as payment on account; do not refund without AR team lead approval.

## Cash discount (Skonto)
Cash discount is allowed only if the value date of the payment is within the discount period of
the invoice's payment terms (Zahlungsbedingung).
- Discount taken within the period: accept, no further action.
- Discount taken after the period (unearned cash discount, unberechtigter Skontoabzug): the
  difference stays open as residual. Above tolerance: dispute reason UNEARNED_DISCOUNT and a polite
  request to pay the difference. Within tolerance: write-off proposal.

## Deductions and short payments
Every deduction above tolerance needs a dispute case with exactly one reason code. Never accept a
deduction as a write-off because the customer says so.
- Create a payment advice for the amount actually paid, listing the invoices it relates to.
- The deducted amount stays open as residual and is linked to the dispute case.
- Customer-specific deduction rights (for example promotion allowances) apply only as written in
  the customer agreement, including its evidence requirements.

## Promises to pay
- A promise to pay up to 30 days from today can be recorded without credit management approval.
- Longer than 30 days or above EUR 50,000: record the draft and flag it for credit management.
- For customers paying late in more than 50 % of cases, the collector follows up one working day
  after the promised date.

## Approval and segregation of duties
- AR collector approves payment advices, dispute cases and promises to pay prepared by the agent.
- Payment advices above EUR 100,000 need a second approval by the AR team lead (four-eyes
  principle). The approval request must show this flag.
- The agent never approves its own proposals and never contacts the bank.

## Customer communication
Reply in the customer's language (German or English), factual and polite. Quote the SAP document
numbers concerned. Do not admit liability for a disputed amount before validation. Never confirm a
write-off or a credit note that has not been approved. Sign as "Accounts Receivable, BestRun DE".
