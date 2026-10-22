"""Step 2 spike: which AR data does the SAP sandbox actually contain?"""
import collections, gzip, json, pathlib, re, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "scratch"
OUT.mkdir(exist_ok=True)


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


ENV = load_env()
URL = (ENV["SAP_SANDBOX_BASE"].rstrip("/")
       + "/sap/opu/odata/sap/API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube")
FIELDS = ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem",
          "Customer", "CustomerName", "AccountingDocumentType", "PostingDate", "NetDueDate",
          "AmountInTransactionCurrency", "TransactionCurrency", "DebitCreditCode",
          "DocumentReferenceID", "AssignmentReference", "BillingDocument", "SalesDocument",
          "PaymentTerms", "CashDiscount1Percent", "CashDiscount1DueDate", "DunningLevel", "IsCleared"]


def get(params):
    query = "&".join(f"{k}={urllib.parse.quote(v, safe=',' + chr(39))}" for k, v in params.items())
    req = urllib.request.Request(f"{URL}?{query}", headers={
        "APIKey": ENV["SAP_SANDBOX_APIKEY"], "Accept": "application/json", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
        print("HTTP", e.code, raw[:800].decode("utf-8", "replace"))
        sys.exit(1)
    return json.loads(body)["d"]


def fetch(filter_, fields, cap=3000, page=500):
    items, skip, total = [], 0, None
    while True:
        d = get({"$filter": filter_, "$select": ",".join(fields), "$top": str(page),
                 "$skip": str(skip), "$inlinecount": "allpages"})
        batch = d["results"]
        if total is None:
            total = int(d.get("__count", 0))
        items += batch
        skip += page
        if not batch or len(items) >= min(total or cap, cap):
            return items, total


def day(v):
    m = re.search(r"-?\d+", v or "")
    return datetime.fromtimestamp(int(m.group()) / 1000, tz=timezone.utc).date().isoformat() if m else ""


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


open_items, open_total = fetch("FinancialAccountType eq 'D' and IsCleared eq false", FIELDS)
cleared, cleared_total = fetch("FinancialAccountType eq 'D' and IsCleared eq true",
                               FIELDS + ["ClearingDate", "ClearingAccountingDocument"])
(OUT / "open_items.json").write_text(json.dumps(open_items, indent=1, ensure_ascii=False), encoding="utf-8")
(OUT / "cleared_items.json").write_text(json.dumps(cleared, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"Open customer items in sandbox:    {open_total} (fetched {len(open_items)})")
print(f"Cleared customer items in sandbox: {cleared_total} (fetched {len(cleared)})")
summarize(open_items, "OPEN customer items")
summarize(cleared, "CLEARED customer items")
