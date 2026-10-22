"""Step 2b: snapshot the demo customers from the SAP sandbox into s4-ar-mock/snapshot."""
import json
from sap_sandbox import CLEARING_FIELDS, FIELDS, ROOT, fetch, get

CUSTOMERS = ["CASH_APP", "10100001", "10100013", "10100002"]
CLEARED_CAP = 1000
SNAP = ROOT / "s4-ar-mock" / "snapshot"
SNAP.mkdir(parents=True, exist_ok=True)


def clean(row):
    """Drop OData metadata and deferred navigation properties."""
    return {k: v for k, v in row.items() if k != "__metadata" and not isinstance(v, dict)}


def write(name, rows):
    body = "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n"
    (SNAP / name).write_text(body, encoding="utf-8")
    print(f"  wrote {name}: {len(rows)} rows, {len(body) // 1024} KB")


open_rows, cleared_rows, masters = [], [], []
for cust in CUSTOMERS:
    base = f"Customer eq '{cust}' and FinancialAccountType eq 'D'"
    o, n_open = fetch(base + " and IsCleared eq false", FIELDS, cap=5000)
    c, n_cleared = fetch(base + " and IsCleared eq true", FIELDS + CLEARING_FIELDS, cap=CLEARED_CAP)
    master = get({}, path=f"/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_Customer('{cust}')", fail=False)
    name = (o or c or [{}])[0].get("CustomerName", "")
    masters.append(clean(master) if master else {"Customer": cust, "CustomerName": name})
    open_rows += [clean(r) for r in o]
    cleared_rows += [clean(r) for r in c[:CLEARED_CAP]]
    print(f"{cust:<10} {name[:28]:<29} open {len(o)}/{n_open}  "
          f"cleared {min(len(c), CLEARED_CAP)}/{n_cleared}  master {'sandbox' if master else 'fallback'}")

write("open_items.json", open_rows)
write("cleared_items.json", cleared_rows)
write("customers.json", masters)
