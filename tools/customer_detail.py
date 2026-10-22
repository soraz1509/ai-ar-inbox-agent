"""Step 2a.2: open items + payment behaviour for selected sandbox customers.
Usage: python tools/customer_detail.py CUST1 CUST2 ..."""
import json, sys
from datetime import date
from statistics import mean
from sap_sandbox import CLEARING_FIELDS, FIELDS, OUT, day, fetch

SHOW = 15  # open items printed per customer

for cust in sys.argv[1:]:
    base = f"Customer eq '{cust}' and FinancialAccountType eq 'D'"
    open_items, n_open = fetch(base + " and IsCleared eq false", FIELDS, cap=300)
    cleared, n_cleared = fetch(base + " and IsCleared eq true", FIELDS + CLEARING_FIELDS, cap=2000)
    name = (open_items or cleared or [{}])[0].get("CustomerName", "")
    print(f"\n##### {cust}  {name}   open={n_open}  cleared={n_cleared}")

    late = [(date.fromisoformat(day(r["ClearingDate"])) - date.fromisoformat(day(r["NetDueDate"]))).days
            for r in cleared if r["DebitCreditCode"] == "S" and r["ClearingDate"] and r["NetDueDate"]]
    if late:
        print(f"Payment behaviour: {len(late)} cleared invoices, avg days late {mean(late):.1f}, "
              f"paid late {sum(d > 0 for d in late)}, max {max(late)}")

    print(f"{'Document':<11}{'Itm':>4} {'FY':<5}{'Posted':<11}{'NetDue':<11}{'Amount':>13} {'Cur':<4}{'DC':<3}"
          f"{'DocRef':<18}{'Assignment':<20}{'BillDoc':<11}{'SalesDoc':<11}Terms")
    for r in sorted(open_items, key=lambda r: day(r["NetDueDate"]) or "9999")[:SHOW]:
        print(f"{r['AccountingDocument']:<11}{r['AccountingDocumentItem']:>4} {r['FiscalYear']:<5}"
              f"{day(r['PostingDate']):<11}{day(r['NetDueDate']):<11}"
              f"{float(r['AmountInTransactionCurrency']):>13,.2f} {r['TransactionCurrency']:<4}{r['DebitCreditCode']:<3}"
              f"{r['DocumentReferenceID']:<18}{r['AssignmentReference']:<20}{r['BillingDocument']:<11}"
              f"{r['SalesDocument']:<11}{r['PaymentTerms']}")
    if len(open_items) > SHOW:
        print(f"... {len(open_items) - SHOW} more open items saved to file")

    (OUT / f"customer_{cust}.json").write_text(
        json.dumps({"open": open_items, "cleared": cleared}, indent=1, ensure_ascii=False), encoding="utf-8")
