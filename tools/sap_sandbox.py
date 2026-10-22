"""Shared helpers for reading the SAP Business Accelerator Hub sandbox."""
import gzip, json, pathlib, re, sys, urllib.error, urllib.parse, urllib.request
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
CLEARING_FIELDS = ["ClearingDate", "ClearingAccountingDocument"]


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
    """OData V2 '/Date(ms)/' -> 'YYYY-MM-DD' ('' if empty)."""
    m = re.search(r"-?\d+", v or "")
    return datetime.fromtimestamp(int(m.group()) / 1000, tz=timezone.utc).date().isoformat() if m else ""
