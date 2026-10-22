"""Step 2b check: the mock returns the same open items as the sandbox for a customer."""
import json, sys, urllib.parse, urllib.request
from sap_sandbox import FIELDS, ITEMS_PATH, fetch

MOCK = "http://localhost:8001"
cust = sys.argv[1] if len(sys.argv) > 1 else "CASH_APP"
flt = f"Customer eq '{cust}' and FinancialAccountType eq 'D' and IsCleared eq false"

sandbox, _ = fetch(flt, FIELDS, cap=5000)
q = urllib.parse.urlencode({"$filter": flt, "$select": ",".join(FIELDS), "$top": "5000"})
with urllib.request.urlopen(f"{MOCK}{ITEMS_PATH}?{q}") as r:
    mock = [m for m in json.load(r)["d"]["results"] if m.get("source") != "seeded"]

key = lambda r: (r["CompanyCode"], r["FiscalYear"], r["AccountingDocument"], r["AccountingDocumentItem"])
strip = lambda r: {k: v for k, v in r.items() if k not in ("__metadata", "source")}
s = {key(r): strip(r) for r in sandbox}
m = {key(r): strip(r) for r in mock}
diff = [k for k in s.keys() | m.keys() if s.get(k) != m.get(k)]
print(f"{cust}: sandbox {len(s)} items, mock {len(m)} items, differences {len(diff)}")
print("IDENTICAL" if not diff else f"first differences: {diff[:5]}")
