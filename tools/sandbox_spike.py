"""Step 2a: overview of AR data in the SAP sandbox (first 3000 open / cleared customer items)."""
import collections, json
from sap_sandbox import CLEARING_FIELDS, FIELDS, OUT, day, fetch


def summarize(items, title):
    print(f"\n=== {title}: {len(items)} items ===")
    print("Company codes:", dict(collections.Counter(i["CompanyCode"] for i in items)))
    by = collections.defaultdict(list)
    for i in items:
        by[(i["Customer"], i["CustomerName"])].append(i)
    print(f"{'Customer':<11}{'Name':<28}{'Items':>6}{'Debit':>6}{'Credit':>7}{'DocRef':>7}"
          f"{'BillDoc':>8}{'Assign':>7}{'Skonto':>7}  {'Cur':<8}Due from..to")
    for (cust, name), rows in sorted(by.items(), key=lambda kv: -len(kv[1]))[:25]:
        due = sorted(day(r["NetDueDate"]) for r in rows if r["NetDueDate"])
        print(f"{cust:<11}{(name or '')[:27]:<28}{len(rows):>6}"
              f"{sum(r['DebitCreditCode'] == 'S' for r in rows):>6}"
              f"{sum(r['DebitCreditCode'] == 'H' for r in rows):>7}"
              f"{sum(bool(r['DocumentReferenceID']) for r in rows):>7}"
              f"{sum(bool(r['BillingDocument']) for r in rows):>8}"
              f"{sum(bool(r['AssignmentReference']) for r in rows):>7}"
              f"{sum(float(r['CashDiscount1Percent'] or 0) > 0 for r in rows):>7}"
              f"  {','.join(sorted({r['TransactionCurrency'] for r in rows})):<8}"
              f"{due[0] if due else '-'}..{due[-1] if due else '-'}")


if __name__ == "__main__":
    open_items, open_total = fetch("FinancialAccountType eq 'D' and IsCleared eq false", FIELDS)
    cleared, cleared_total = fetch("FinancialAccountType eq 'D' and IsCleared eq true", FIELDS + CLEARING_FIELDS)
    (OUT / "open_items.json").write_text(json.dumps(open_items, indent=1, ensure_ascii=False), encoding="utf-8")
    (OUT / "cleared_items.json").write_text(json.dumps(cleared, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Open customer items in sandbox:    {open_total} (fetched {len(open_items)})")
    print(f"Cleared customer items in sandbox: {cleared_total} (fetched {len(cleared)})")
    summarize(open_items, "OPEN customer items")
    summarize(cleared, "CLEARED customer items")
